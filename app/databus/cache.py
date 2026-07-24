"""
DataBus Cache Layer - Three-Tier Cache with SWR + Per-Type Stats
================================================================

L1: In-memory dict (sub-millisecond, 4096 keys, LRU eviction) + SWR stale buffer
L2: Redis (sub-millisecond, shared across processes, TTL-managed)
L3: Cloudflare R2 cold storage (RAG permanence, nightly snapshots)

Stale-While-Revalidate (SWR):
  - L1 stores both fresh and stale entries (stale = TTL * 2)
  - On cache read, if entry is fresh → direct hit
  - If entry is stale (past TTL but within stale window) → return stale data
    AND flag for background refresh via cache.stale_refresh_callback
  - User NEVER waits for a refresh - always gets instant data

Per-Type Stats:
  - Tracks hits/misses per data_type for tuning TTLs
  - health() flags types with hit_rate < 30% as "increase TTL"
"""

from __future__ import annotations
import asyncio
import contextlib
import hashlib
import json
import os
import time
from collections import OrderedDict, defaultdict
from typing import TYPE_CHECKING, Any

import httpx

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger("databus.cache")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_DB = int(os.getenv("REDIS_DB", "0"))

class L1Cache:
    """In-memory LRU cache with Stale-While-Revalidate support.

    Entries are stored with TWO expiry windows:
      - fresh_expiry: data is fresh, return immediately (normal hit)
      - stale_expiry: data is stale but usable (SWR hit)
    Stale entries are returned instantly; the caller triggers background refresh.
    """

    def __init__(self, max_keys: int = 4096, stale_multiplier: float = 2.5) -> None:
        self._max = max_keys
        self._stale_mult = stale_multiplier
        self._store: Ordereddict[str, tuple[Any, float, float]] = OrderedDict()
        # (value, fresh_expiry, stale_expiry)
        self.hits = 0
        self.stale_hits = 0
        self.misses = 0
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> tuple[Any | None, bool]:
        """Returns (value, is_stale). is_stale=True means background refresh needed."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None, False
            value, fresh_expiry, stale_expiry = entry
            now = time.monotonic()
            if now > stale_expiry:
                # Fully expired - evict
                del self._store[key]
                self.misses += 1
                return None, False
            if now > fresh_expiry:
                # Stale but usable - SWR hit
                self._store.move_to_end(key)
                self.stale_hits += 1
                return value, True
            # Fresh hit
            self._store.move_to_end(key)
            self.hits += 1
            return value, False

    async def set(self, key: str, value: Any, ttl: int) -> None:
        async with self._lock:
            now = time.monotonic()
            fresh = now + ttl
            stale = now + int(ttl * self._stale_mult)
            self._store[key] = (value, fresh, stale)
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        total = self.hits + self.stale_hits + self.misses
        return {
            "keys": len(self._store),
            "max_keys": self._max,
            "hits": self.hits,
            "stale_hits": self.stale_hits,
            "misses": self.misses,
            "hit_rate": round((self.hits + self.stale_hits) / total * 100, 1) if total > 0 else 0,
            "fresh_hit_rate": round(self.hits / total * 100, 1) if total > 0 else 0,
        }

class L2RedisCache:
    """Redis cache. Shared across processes. TTL-managed automatically."""

    def __init__(self) -> None:
        self._redis = None
        self._available = False
        self._prefix = "databus:"
        self.hits = 0
        self.misses = 0

    async def _connect(self) -> bool:
        if self._redis and self._available:
            return True
        try:
            import redis.asyncio as aioredis

            kwargs = {
                "host": REDIS_HOST,
                "port": REDIS_PORT,
                "db": REDIS_DB,
                "socket_connect_timeout": 2,
                "socket_timeout": 2,
                "decode_responses": True,
                "protocol": 2,  # Redis 7.2 compat - avoid HELLO/AUTH handshake issue
            }
            if REDIS_PASSWORD:
                kwargs["password"] = REDIS_PASSWORD
            self._redis = aioredis.Redis(**kwargs)
            await self._redis.ping()
            self._available = True
            logger.info("DataBus Cache: Redis connected")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"DataBus Cache: Redis unavailable ({e}), L2 disabled")
            self._available = False
            return False

    async def get(self, key: str) -> Any | None:
        if not self._available and not await self._connect():
            self.misses += 1
            return None
        try:
            raw = await self._redis.get(f"{self._prefix}{key}")
            if raw:
                self.hits += 1
                return json.loads(raw)
            self.misses += 1
            return None
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            self._available = False
            self.misses += 1
            return None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        if not self._available and not await self._connect():
            return
        try:
            await self._redis.setex(f"{self._prefix}{key}", ttl, json.dumps(value, default=str))
        except (ConnectionError, OSError, httpx.HTTPError, json.JSONDecodeError):  # noqa: BLE001
            self._available = False

    async def delete(self, key: str) -> None:
        if not self._available:
            return
        with contextlib.suppress(Exception):
            await self._redis.delete(f"{self._prefix}{key}")

    async def clear(self) -> None:
        if not self._available:
            return
        try:
            async for key in self._redis.scan_iter(f"{self._prefix}*"):
                await self._redis.delete(key)
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException):
            pass

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "available": self._available,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total * 100, 1) if total > 0 else 0,
        }

class CacheLayer:
    """
    Three-tier cache with Stale-While-Revalidate + per-type stats.

    L1 (memory, SWR) → L2 (Redis) → L3 (R2, async, background)

    Read path: L1 (fresh? → done. stale? → return stale + schedule refresh) → L2 → miss
    Write path: External API → L1 + L2 (L3 batched via RAG permanence cron)

    SWR callback: When L1 returns stale data, cache fires stale_refresh_callback
    so the caller can schedule background re-fetch without blocking the user.
    """

    def __init__(self) -> None:
        self.l1 = L1Cache(max_keys=4096)
        self.l2 = L2RedisCache()
        self._l3_enabled = True
        # SWR: caller sets this to a coroutine factory for background refresh
        self.stale_refresh_callback: Callable | None = None
        # Per-type hit/miss tracking for TTL tuning
        self._type_stats: dict[str, dict[str, int]] = defaultdict(
            lambda: {"hits": 0, "stale_hits": 0, "misses": 0}
        )
        # Data type TTLs - optimized for FREE tier usage to avoid rate limits
        # and maximize our 1-month Arkham trial + Alchemy free quota.
        self.ttl_config = {
            # ── High Frequency (Cache aggressively to save free API calls) ──
            "token_price": 60,  # Increased from 15s to 60s (better cache reuse for price data)
            "market_overview": 60,  # Increased from 30s to 60s
            "trending": 120,  # Increased from 60s to 120s
            "market_movers": 60,  # Increased from 30s to 60s
            "alerts": 30,  # Increased from 15s to 30s
            # ── Medium Frequency ──
            "token_detail": 60,  # Increased from 30s
            "token_meta": 300,  # Increased from 120s (Alchemy free tier)
            "wallet_balance": 30,  # Increased from 10s (Alchemy free tier)
            "wallet_tokens": 300,  # Increased from 60s to 300s (reduce misses on uncached provider)
            "wallet_pnl": 120,  # Increased from 60s
            "tx_history": 60,  # Increased from 30s
            "dex_data": 60,  # Increased from 30s
            "holder_data": 120,  # Increased from 60s
            # ── Low Frequency (Static or slow-moving data) ──
            "risk_scan": 600,  # Increased from 300s
            "sentinel_deep": 600,  # Increased from 300s
            "funding_source": 7200,  # Increased from 3600s (2 hours)
            "solana_funding": 7200,  # Increased from 3600s (2 hours)
            "wallet_labels": 86400,  # 24 hours (local data, no API cost)
            "entity_intel": 3600,  # Increased from 1800s (1 hour)
            "socialfi_resolve": 86400,  # 24 hours
            "cross_chain": 3600,  # Increased from 1800s
            "wallet_cluster": 3600,  # Increased from 1800s
            "bundle_detect": 600,  # Increased from 300s
            # ── ARKHAM INTELLIGENCE (1-Month Free Trial: 100k quota) ──
            # Cache aggressively to maximize the 100,000 monthly request limit
            "arkham_entity": 600,  # Increased from 300s (10 mins)
            "arkham_portfolio": 300,  # Increased from 120s (5 mins)
            "arkham_labels": 7200,  # Increased from 3600s (2 hours)
            "arkham_transfers": 300,  # Increased from 60s (5 mins)
            "arkham_counterparties": 600,  # Increased from 300s (10 mins)
            # ── Other ──
            "nansen_labels": 3600,
            "nansen_smart_money": 1800,
            "news": 600,  # Increased from 300s (10 mins)
            "news_intel": 600,  # 10 mins for aggregated news
            "messari_news": 900,  # 15 mins for Messari institutional feed
            "social_feed": 300,  # Increased from 120s (5 mins)
            "sentiment": 600,  # Increased from 300s
            "whale_data": 300,  # Increased from 120s
            "smart_money": 300,  # Increased from 120s
            "gmgn_smart_money": 300,  # Increased from 120s
            "launches": 120,  # Increased from 60s
            "rugmap": 600,  # Increased from 300s
            "rugmaps_analysis": 1200,  # Increased from 600s (20 mins)
            "contract_scan": 3600,  # Increased from 1800s (1 hour)
            "threat_check": 600,  # Increased from 300s
            "prediction_markets": 120,  # Increased from 60s
            "prediction_signals": 300,  # Increased from 120s
            "defi_protocols": 600,  # Increased from 300s
            "rag_search": 600,  # Increased from 300s
            "tvl": 300,  # Increased from 120s
            "wallet_profile": 600,  # Increased from 300s
            "portfolio": 120,  # Increased from 60s
            # ── NEW FREE SOURCES ADDED ──
            "defillama_tvl": 3600,  # 1 hour (completely free, no API key)
            "defillama_chains": 3600,  # 1 hour (completely free, no API key)
            "blockchair_address": 600,  # 10 mins (free tier, no API key)
            "blockchair_stats": 1800,  # 30 mins (free tier, no API key)
            "birdeye_overview": 120,  # 2 mins (freemium, 50k/mo quota)
            "birdeye_price": 30,  # 30s (freemium, 50k/mo quota, fallback to DexScreener)
            "solana_tracker_price": 15,  # 15s (freemium, 5k/mo quota)
            "solana_tracker_token": 60,  # 1 min (freemium, 5k/mo quota)
            "solana_tracker_trending": 120,  # 2 mins (freemium, 5k/mo quota)
            "dev_activity": 3600,  # 1 hour (Santiment free tier, 100 calls/day)
            "url_security_scan": 86400,  # 24 hours (VirusTotal free tier, 500 req/day)
            "dune_early_buyers": 14400,  # 4 hours (Dune free tier: 10k CU/mo, aggressive caching)
            "default": 60,
        }

    def _extract_data_type(self, key: str) -> str:
        """Extract data_type from cache key format 'source:data_type:hash'."""
        parts = key.split(":")
        if len(parts) >= 2:
            return parts[1]
        return "default"

    async def get(self, key: str, data_type: str = "default") -> tuple[Any | None, bool]:
        """Get from cache with SWR. Returns (value, is_stale).
        If is_stale=True, caller should schedule background refresh.
        """
        # L1 (with SWR)
        val, is_stale = await self.l1.get(key)
        if val is not None:
            dtype = data_type or self._extract_data_type(key)
            if is_stale:
                self._type_stats[dtype]["stale_hits"] += 1
            else:
                self._type_stats[dtype]["hits"] += 1
            # SWR: schedule background refresh if stale and callback is set
            if is_stale and self.stale_refresh_callback:
                try:  # noqa: SIM105
                    asyncio.create_task(self.stale_refresh_callback(key))
                except (ConnectionError, OSError, TimeoutError):  # noqa: BLE001
                    pass  # Best-effort refresh
            return val, is_stale
        # L2 (no SWR - Redis handles its own TTL)
        val = await self.l2.get(key)
        if val is not None:
            # Promote to L1 with shorter TTL (fresh only for now)
            await self.l1.set(key, val, 60)
            dtype = data_type or self._extract_data_type(key)
            self._type_stats[dtype]["hits"] += 1
            return val, False
        dtype = data_type or self._extract_data_type(key)
        self._type_stats[dtype]["misses"] += 1
        return None, False

    async def set(
        self, key: str, value: Any, ttl: int | None = None, data_type: str = "default"
    ) -> None:
        """Set in cache. Writes to L1 + L2."""
        if ttl is None:
            ttl = self.ttl_config.get(data_type, 60)
        await self.l1.set(key, value, ttl)
        await self.l2.set(key, value, ttl)

    async def delete(self, key: str) -> None:
        await self.l1.delete(key)
        await self.l2.delete(key)

    async def clear(self) -> None:
        await self.l1.clear()
        await self.l2.clear()

    def make_key(self, source: str, data_type: str, **kwargs) -> str:
        """Generate a deterministic cache key."""
        args_str = json.dumps(kwargs, sort_keys=True, default=str)
        args_hash = hashlib.sha256(args_str.encode()).hexdigest()[:16]
        return f"{source}:{data_type}:{args_hash}"

    def type_stats(self) -> dict[str, dict]:
        """Per-data-type hit/miss/stale stats with TTL tuning suggestions."""
        result = {}
        for dtype, counts in self._type_stats.items():
            total = counts["hits"] + counts["stale_hits"] + counts["misses"]
            hit_rate = (
                round((counts["hits"] + counts["stale_hits"]) / total * 100, 1) if total > 0 else 0
            )
            entry = {
                "hits": counts["hits"],
                "stale_hits": counts["stale_hits"],
                "misses": counts["misses"],
                "hit_rate": hit_rate,
                "current_ttl": self.ttl_config.get(dtype, 60),
            }
            # Suggest TTL increase if hit_rate < 30%
            if total > 10 and hit_rate < 30:
                entry["suggestion"] = "increase_ttl"
            # Suggest TTL decrease if hit_rate > 95% and TTL > 120
            elif total > 10 and hit_rate > 95 and self.ttl_config.get(dtype, 60) > 120:
                entry["suggestion"] = "decrease_ttl"
            result[dtype] = entry
        return result

    async def health(self) -> dict:
        l1 = self.l1.stats()
        l2 = self.l2.stats()
        total_hits = l1["hits"] + l1["stale_hits"] + l2["hits"]
        total_misses = l1["misses"] + l2["misses"]
        total = total_hits + total_misses
        return {
            "status": "ok",
            "l1_memory": l1,
            "l2_redis": l2,
            "l3_r2": {"enabled": self._l3_enabled, "note": "Batched via RAG permanence cron"},
            "combined_hit_rate": round(total_hits / total * 100, 1) if total > 0 else 0,
            "total_hits": total_hits,
            "total_misses": total_misses,
            "per_type_stats": self.type_stats(),
            "ttl_config": self.ttl_config,
        }

# ── Singleton ─────────────────────────────────────────────────────────────────

_cache: CacheLayer | None = None

def get_cache() -> CacheLayer:
    global _cache
    if _cache is None:
        _cache = CacheLayer()
    return _cache
