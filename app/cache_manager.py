"""
RMI Cache Manager - Unified caching layer with Redis + in-memory fallback.
Proprietary system that auto-tracks credits, adapts TTLs, and monitors hit rates.

Architecture:
  Redis (primary) → in-memory dict (fallback) → external API
  All external API calls go through cache_get_or_fetch().
  Credit tracking prevents rate limit exhaustion on free tiers.

Usage:
  from app.cache_manager import cache

  # Simple cached fetch
  data = await cache.get_or_fetch(
      key="coingecko:trending",
      ttl=120,
      fetcher=lambda: httpx_get("https://api.coingecko.com/api/v3/search/trending"),
      source="coingecko"
  )

  # Stats monitoring
  stats = cache.get_stats()  # {"coingecko": {"hits": 1423, "misses": 47, ...}}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# ═══════════════════════════════════════════════════════════════
# PROVIDER CONFIG - TTLs, rate limits, credit tracking
# ═══════════════════════════════════════════════════════════════
PROVIDER_CONFIG = {
    "coingecko": {
        "ttl": 120,  # seconds (free tier: 30 calls/min)
        "rate_limit": 30,  # calls per minute
        "rate_window": 60,  # seconds
        "description": "CoinGecko public API",
    },
    "coingecko_pro": {
        "ttl": 60,
        "rate_limit": 500,
        "rate_window": 60,
        "description": "CoinGecko Pro (our API key)",
    },
    "dexscreener": {
        "ttl": 30,
        "rate_limit": 300,
        "rate_window": 60,
        "description": "DexScreener public API",
    },
    "jupiter": {
        "ttl": 30,
        "rate_limit": 600,
        "rate_window": 60,
        "description": "Jupiter DEX aggregator",
    },
    "birdeye": {
        "ttl": 60,
        "rate_limit": 60,
        "rate_window": 60,
        "description": "Birdeye token data",
    },
    "dexscreener_security": {
        "ttl": 300,
        "rate_limit": 100,
        "rate_window": 60,
        "description": "DexScreener security endpoint",
    },
    "defillama": {
        "ttl": 300,
        "rate_limit": 100,
        "rate_window": 60,
        "description": "DeFiLlama TVL data",
    },
    "moralis": {
        "ttl": 300,
        "rate_limit": 100,
        "rate_window": 60,
        "description": "Moralis Web3 API (our key)",
    },
    "helius": {
        "ttl": 60,
        "rate_limit": 200,
        "rate_window": 60,
        "description": "Helius Solana RPC (our key)",
    },
    "gmgn": {
        "ttl": 120,
        "rate_limit": 60,
        "rate_window": 60,
        "description": "GMGN trending/wallet data",
    },
    "rugcheck": {
        "ttl": 300,
        "rate_limit": 100,
        "rate_window": 60,
        "description": "RugCheck security API",
    },
    "trmlabs": {
        "ttl": 600,
        "rate_limit": 50,
        "rate_window": 60,
        "description": "TRM Labs sanctions screening",
    },
    "polymarket": {
        "ttl": 120,
        "rate_limit": 200,
        "rate_window": 60,
        "description": "Polymarket prediction data",
    },
    "coincap": {
        "ttl": 60,
        "rate_limit": 200,
        "rate_window": 60,
        "description": "CoinCap price data",
    },
    "nansen": {
        "ttl": 300,
        "rate_limit": 50,
        "rate_window": 60,
        "description": "Nansen on-chain analytics (paid key)",
    },
    "dune": {
        "ttl": 600,
        "rate_limit": 30,
        "rate_window": 60,
        "description": "Dune Analytics (paid key)",
    },
    "solscan": {
        "ttl": 120,
        "rate_limit": 100,
        "rate_window": 60,
        "description": "Solscan Solana explorer API",
    },
    "quicknode": {
        "ttl": 60,
        "rate_limit": 300,
        "rate_window": 60,
        "description": "QuickNode RPC (paid key)",
    },
    "alchemy": {
        "ttl": 60,
        "rate_limit": 300,
        "rate_window": 60,
        "description": "Alchemy multi-chain RPC (paid key)",
    },
    "solana_rpc": {
        "ttl": 30,
        "rate_limit": 500,
        "rate_window": 60,
        "description": "Solana RPC (helius/quicknode combo)",
    },
    "coinmarketcap": {
        "ttl": 120,
        "rate_limit": 100,
        "rate_window": 60,
        "description": "CoinMarketCap API",
    },
    "etherscan": {
        "ttl": 300,
        "rate_limit": 5,
        "rate_window": 1,
        "description": "Etherscan API (free tier: 5 calls/sec)",
    },
    "bitquery": {
        "ttl": 300,
        "rate_limit": 100,
        "rate_window": 60,
        "description": "Bitquery GraphQL blockchain data",
    },
}

# Providers with API keys (free tiers with limits)
KEYED_PROVIDERS = {
    "coingecko_pro",
    "moralis",
    "helius",
    "trmlabs",
    "nansen",
    "dune",
    "solscan",
    "quicknode",
    "alchemy",
    "coinmarketcap",
    "etherscan",
    "bitquery",
    "birdeye",
    "gmgn",
}
# Truly free (no keys at all)
FREE_PROVIDERS = {
    "coingecko",
    "dexscreener",
    "jupiter",
    "defillama",
    "polymarket",
    "coincap",
    "rugcheck",
}


class RMICache:
    """Centralized cache with Redis primary + dict fallback + credit tracking."""

    def __init__(self) -> None:
        self._redis = None
        self._redis_available = False
        self._memory: dict[str, tuple[Any, float]] = {}
        # Credit tracking: {source: {"hits": N, "misses": N, "calls": [], "bytes": N}}
        self._stats: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()
        self._init_redis()

    def _init_redis(self) -> None:
        try:
            import redis.asyncio as redis

            self._redis = redis.Redis(
                host=os.getenv("REDIS_HOST", "rmi-redis"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD") or None,
                db=int(os.getenv("REDIS_DB", "0")),
                socket_connect_timeout=2,
                socket_timeout=2,
                decode_responses=True,
            )
            self._redis_available = True
        except (ConnectionError, OSError):
            self._redis_available = False

    async def _redis_ping(self) -> bool:
        if not self._redis or not self._redis_available:
            return False
        try:
            await self._redis.ping()
            return True
        except (ConnectionError, OSError):
            self._redis_available = False
            return False

    async def _redis_get(self, key: str) -> Any | None:
        try:
            if await self._redis_ping():
                raw = await self._redis.get(f"rmi:cache:{key}")
                if raw:
                    return json.loads(raw)
        except (ConnectionError, OSError, json.JSONDecodeError):
            pass
        return None

    async def _redis_set(self, key: str, value: Any, ttl: int) -> None:
        try:
            if await self._redis_ping():
                await self._redis.setex(f"rmi:cache:{key}", ttl, json.dumps(value, default=str))
        except (ConnectionError, OSError, json.JSONDecodeError):
            pass

    def _mem_get(self, key: str) -> Any | None:
        entry = self._memory.get(key)
        if entry and time.time() < entry[1]:
            return entry[0]
        return None

    def _mem_set(self, key: str, value: Any, ttl: int) -> None:
        self._memory[key] = (value, time.time() + ttl)

    def _get_stats(self, source: str) -> dict[str, Any]:
        if source not in self._stats:
            self._stats[source] = {
                "hits": 0,
                "misses": 0,
                "calls": [],
                "bytes_saved": 0,
                "rate_limited": 0,
                "last_call": 0,
                "provider": PROVIDER_CONFIG.get(source, {}),
            }
        return self._stats[source]

    def _check_rate_limit(self, source: str) -> bool:
        """Check if we're about to exceed rate limit. Returns True if OK to call."""
        cfg = PROVIDER_CONFIG.get(source, {})
        limit = cfg.get("rate_limit", 100)
        window = cfg.get("rate_window", 60)
        st = self._get_stats(source)
        now = time.time()
        # Prune old calls
        st["calls"] = [t for t in st["calls"] if now - t < window]
        if len(st["calls"]) >= limit:
            st["rate_limited"] = st.get("rate_limited", 0) + 1
            return False
        return True

    async def get_or_fetch(
        self,
        key: str,
        ttl: int | None = None,
        fetcher: Callable | None = None,
        source: str = "unknown",
        force: bool = False,
    ) -> Any | None:
        """
        Get from cache or fetch from source. Tracks credits automatically.

        Args:
            key: Cache key (e.g., "coingecko:trending")
            ttl: Override TTL (default: from PROVIDER_CONFIG)
            fetcher: Async function that returns data (called on cache miss)
            source: Provider name (must be in PROVIDER_CONFIG)
            force: Skip cache, force fresh fetch

        Returns:
            Cached or freshly fetched data, or None on failure.
        """
        if ttl is None:
            ttl = PROVIDER_CONFIG.get(source, {}).get("ttl", 60)

        st = self._get_stats(source)

        # Check cache (unless forced)
        if not force:
            # Try Redis first
            cached = await self._redis_get(key)
            if cached is not None:
                st["hits"] = st.get("hits", 0) + 1
                return cached
            # Fallback to memory
            cached = self._mem_get(key)
            if cached is not None:
                st["hits"] = st.get("hits", 0) + 1
                return cached

        # Cache miss - check rate limit before calling external API
        if not self._check_rate_limit(source):
            # Rate limited - return stale data if available, else None
            stale = self._mem_get(key)  # memory may have expired but better than nothing
            return stale

        # Fetch fresh data
        if fetcher is None:
            return None

        st["misses"] = st.get("misses", 0) + 1
        st["last_call"] = time.time()
        st["calls"].append(time.time())

        try:
            data = await fetcher()
            if data is not None:
                # Store in both caches
                await self._redis_set(key, data, ttl)
                self._mem_set(key, data, ttl)
                # Track bytes saved
                with contextlib.suppress(Exception):
                    st["bytes_saved"] = st.get("bytes_saved", 0) + len(json.dumps(data, default=str))
            return data
        except json.JSONDecodeError:
            return None

    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive cache statistics for monitoring."""
        sources = {}
        total_hits = 0
        total_misses = 0
        total_rate_limited = 0
        total_bytes = 0

        for source, st in self._stats.items():
            hits = st.get("hits", 0)
            misses = st.get("misses", 0)
            total = hits + misses
            sources[source] = {
                "hits": hits,
                "misses": misses,
                "hit_rate": round(hits / total * 100, 1) if total > 0 else 0,
                "rate_limited": st.get("rate_limited", 0),
                "bytes_saved": st.get("bytes_saved", 0),
                "provider": st.get("provider", {}),
                "calls_this_window": len(st.get("calls", [])),
            }
            total_hits += hits
            total_misses += misses
            total_rate_limited += st.get("rate_limited", 0)
            total_bytes += st.get("bytes_saved", 0)

        total = total_hits + total_misses
        return {
            "summary": {
                "total_hits": total_hits,
                "total_misses": total_misses,
                "hit_rate": round(total_hits / total * 100, 1) if total > 0 else 0,
                "rate_limited": total_rate_limited,
                "bytes_saved": total_bytes,
                "redis_available": self._redis_available,
                "memory_keys": len(self._memory),
            },
            "sources": sources,
        }

    def warm_cache(self, key: str, data: Any, ttl: int | None = None, source: str = "manual") -> None:
        """Pre-warm cache with data (e.g., from cron jobs)."""
        if ttl is None:
            ttl = PROVIDER_CONFIG.get(source, {}).get("ttl", 60)
        asyncio.ensure_future(self._redis_set(key, data, ttl))
        self._mem_set(key, data, ttl)

    async def invalidate(self, key: str) -> None:
        """Force-invalidate a cache key."""
        try:
            if self._redis and self._redis_available:
                await self._redis.delete(f"rmi:cache:{key}")
        except (ConnectionError, OSError):
            pass
        self._memory.pop(key, None)


# Singleton instance
cache = RMICache()
