"""
Aggressive Caching Shield - Solana Tracker Data API Client
Multi-key load balanced with per-key rate limiting and quota tracking.

Keys configured via env vars: SOLANA_TRACKER_PRIMARY_HOST, SOLANA_TRACKER_SECONDARY_HOST, SOLANA_TRACKER_API_KEY
Load balancing: round-robin with 429 fallback to next key
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger

logger = get_logger("solana_tracker")

# Key configs
KEY_CONFIGS = [
    {
        "name": "primary",
        "base_url": "https://noble-flint-3959.secure.data.solanatracker.io",
        "api_key": None,
        "rate_rps": 3.0,
        "monthly_quota": 2500,
    },
    {
        "name": "secondary",
        "base_url": "https://data.solanatracker.io",
        "api_key": os.getenv("SOLANA_TRACKER_API_KEY", ""),
        "rate_rps": 3.0,
        "monthly_quota": 2500,
    },
]

# TTL tiers (seconds) - cached aggressively to maximize free tier
TTL_PRICE = 8
TTL_TOKEN = 30
TTL_WALLET = 20
TTL_TRADES = 20
TTL_OHLCV = 60
TTL_SEARCH = 45
TTL_LATEST = 10
TTL_TRENDING = 180
TTL_HOLDERS = 60

@dataclass
class KeyState:
    """Per-key usage and health tracking."""

    name: str
    base_url: str
    api_key: str | None
    rate_rps: float
    monthly_quota: int
    calls_this_month: int = 0
    month_key: str = ""
    tokens: float = 0.0
    last_refill: float = 0.0
    rate_limited_until: float = 0.0
    consecutive_429s: int = 0
    http: httpx.AsyncClient | None = None

    def __post_init__(self) -> None:
        self.tokens = self.rate_rps
        self.last_refill = time.monotonic()
        self.month_key = time.strftime("%Y-%m")

    def can_use(self, now: float) -> bool:
        if now < self.rate_limited_until:
            return False
        current_month = time.strftime("%Y-%m")
        if current_month != self.month_key:
            self.month_key = current_month
            self.calls_this_month = 0
        return self.calls_this_month < self.monthly_quota

    def refill(self, now: float) -> None:
        elapsed = now - self.last_refill
        self.tokens = min(self.rate_rps, self.tokens + elapsed * self.rate_rps)
        self.last_refill = now

    def mark_used(self) -> None:
        self.tokens -= 1.0
        self.calls_this_month += 1

    def mark_429(self, now: float) -> None:
        self.consecutive_429s += 1
        backoff = min(60, 5 * (2 ** min(self.consecutive_429s, 4)))
        self.rate_limited_until = now + backoff

    def mark_success(self) -> None:
        self.consecutive_429s = 0
        self.rate_limited_until = 0.0

class SolanaTrackerClient:
    """Multi-key load balanced client for Solana Tracker Data API.

    Round-robins between PRIMARY (secure endpoint) and SECONDARY (generic).
    On 429, falls through to next key with exponential backoff.
    """

    def __init__(self) -> None:
        self._keys = [KeyState(**cfg) for cfg in KEY_CONFIGS]
        self._key_index = 0
        self._key_lock = asyncio.Lock()
        self._l1: dict[str, tuple] = {}
        self._l1_lock = asyncio.Lock()
        self.cache_hits = 0
        self.cache_misses = 0

    def _cache_key(self, path: str, params: dict) -> str:
        raw = f"{path}:{json.dumps(params or {}, sort_keys=True, default=str)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    async def _cache_get(self, key: str) -> dict | None:
        async with self._l1_lock:
            entry = self._l1.get(key)
            if entry:
                expiry, data = entry
                if time.monotonic() < expiry:
                    self.cache_hits += 1
                    return data
                del self._l1[key]
        self.cache_misses += 1
        return None

    async def _cache_set(self, key: str, data: dict, ttl: int) -> None:
        async with self._l1_lock:
            self._l1[key] = (time.monotonic() + ttl, data)
            if len(self._l1) > 1024:
                oldest = min(self._l1.keys(), key=lambda k: self._l1[k][0])
                del self._l1[oldest]

    async def _get_http(self, key: KeyState) -> httpx.AsyncClient:
        if key.http is None:
            headers = {"Accept": "application/json"}
            if key.api_key:
                headers["x-api-key"] = key.api_key
            key.http = httpx.AsyncClient(timeout=15.0, headers=headers)
        return key.http

    async def _pick_key(self) -> KeyState | None:
        async with self._key_lock:
            now = time.monotonic()
            for _ in range(len(self._keys)):
                key = self._keys[self._key_index]
                self._key_index = (self._key_index + 1) % len(self._keys)
                key.refill(now)
                if not key.can_use(now):
                    continue
                if key.tokens < 1.0:
                    continue
                key.mark_used()
                return key
            return None

    async def _call(
        self, path: str, params: dict | None = None, ttl: int = TTL_TOKEN
    ) -> dict | None:
        cache_key = self._cache_key(path, params or {})
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        for _attempt in range(min(len(self._keys), 2)):
            key = await self._pick_key()
            if key is None:
                logger.warning("ST: all keys exhausted")
                return None

            http = await self._get_http(key)
            url = f"{key.base_url}{path}"
            now = time.monotonic()

            try:
                if params:
                    clean = {k: v for k, v in params.items() if v is not None}
                    resp = await http.get(url, params=clean)
                else:
                    resp = await http.get(url)

                if resp.status_code == 200:
                    key.mark_success()
                    data = resp.json()
                    await self._cache_set(cache_key, data, ttl)
                    return data
                elif resp.status_code == 429:
                    key.mark_429(now)
                    logger.debug(f"ST 429 on {key.name}, rotating")
                    continue
                elif resp.status_code == 401:
                    logger.error(f"ST 401 on {key.name}, key invalid")
                    key.rate_limited_until = now + 3600
                    continue
                else:
                    logger.warning(f"ST {resp.status_code} on {key.name}: {resp.text[:100]}")
                    return None
            except httpx.TimeoutException:
                logger.debug(f"ST timeout on {key.name}, rotating")
                continue
            except Exception as e:
                logger.warning(f"ST error on {key.name}: {e}")
                continue

        logger.warning(f"ST: all attempts failed for {path}")
        return None

    # Token Endpoints
    async def get_price(self, mint: str) -> dict | None:
        return await self._call("/price", {"token": mint}, TTL_PRICE)

    async def get_token(self, mint: str) -> dict | None:
        return await self._call(f"/tokens/{mint}", None, TTL_TOKEN)

    async def search_tokens(self, **kwargs) -> dict | None:
        params = {k: v for k, v in kwargs.items() if v is not None}
        params.setdefault("limit", 20)
        return await self._call("/search", params, TTL_SEARCH)

    async def get_tokens_latest(self, limit: int = 20) -> dict | None:
        return await self._call("/tokens/latest", {"limit": min(limit, 100)}, TTL_LATEST)

    async def get_tokens_trending(self, limit: int = 20) -> dict | None:
        return await self._call("/tokens/trending", {"limit": min(limit, 100)}, TTL_TRENDING)

    async def get_token_holders(self, mint: str, limit: int = 20) -> dict | None:
        return await self._call(f"/tokens/{mint}/holders", {"limit": min(limit, 50)}, TTL_HOLDERS)

    async def get_token_trades(
        self, mint: str, limit: int = 20, cursor: str | None = None
    ) -> dict | None:
        params = {"limit": min(limit, 100)}
        if cursor:
            params["cursor"] = cursor
        return await self._call(f"/trades/{mint}", params, TTL_TRADES)

    # Wallet
    async def get_wallet(self, address: str) -> dict | None:
        return await self._call(f"/wallet/{address}", None, TTL_WALLET)

    async def get_wallet_trades(
        self, address: str, limit: int = 20, cursor: str | None = None
    ) -> dict | None:
        params = {"limit": min(limit, 100)}
        if cursor:
            params["cursor"] = cursor
        return await self._call(f"/wallet/{address}/trades", params, TTL_TRADES)

    async def get_wallet_pnl(self, address: str) -> dict | None:
        return await self._call(f"/wallet/{address}/pnl", None, TTL_WALLET)

    # Charts
    async def get_ohlcv(self, mint: str, timeframe: str = "1H", limit: int = 100) -> dict | None:
        return await self._call(
            f"/ohlcv/{mint}",
            {
                "timeframe": timeframe,
                "limit": min(limit, 500),
            },
            TTL_OHLCV,
        )

    # Stats
    def stats(self) -> dict:
        now = time.monotonic()
        keys_info = []
        for k in self._keys:
            k.refill(now)
            keys_info.append(
                {
                    "name": k.name,
                    "tokens": round(k.tokens, 1),
                    "calls_this_month": k.calls_this_month,
                    "quota_remaining": k.monthly_quota - k.calls_this_month,
                    "rate_limited": k.rate_limited_until > now,
                    "consecutive_429s": k.consecutive_429s,
                }
            )

        def _get() -> dict:
            return {
                "keys": keys_info,
                "cache_hits": self.cache_hits,
                "cache_misses": self.cache_misses,
                "l1_size": len(self._l1),
                "combined_quota": sum(k.monthly_quota for k in self._keys),
                "combined_used": sum(k.calls_this_month for k in self._keys),
            }

        return _get()

_client: SolanaTrackerClient | None = None

def get_solana_tracker() -> SolanaTrackerClient:
    global _client
    if _client is None:
        _client = SolanaTrackerClient()
    return _client
