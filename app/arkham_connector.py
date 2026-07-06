"""
Arkham Intelligence API Connector
Entity labeling, wallet attribution, institutional tracking, sanctions screening.
Base URL: https://api.arkhamintelligence.com
Auth header: API-Key (from /root/.secrets/arkham_api_key or ARKHAM_API_KEY env var)
"""

import asyncio
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Auth ────────────────────────────────────────────────────────────────────
ARKHAM_API_KEY = os.getenv("ARKHAM_API_KEY", "").strip()
if not ARKHAM_API_KEY:
    # Fallback to reading from secrets file
    _secrets_paths = ["/root/.secrets/arkham_api_key"]
    for _sp in _secrets_paths:
        if os.path.exists(_sp):
            with open(_sp) as _f:
                ARKHAM_API_KEY = _f.read().strip()
            break

BASE_URL = "https://api.arkhamintelligence.com"


# ── Simple TTL Cache ────────────────────────────────────────────────────────
class _TTLCache:
    """In-memory cache with per-key TTL for rate-limited API responses."""

    def __init__(self, default_ttl: int = 120):
        self._store: dict[str, tuple[Any, float]] = {}
        self._ttl = default_ttl

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires = entry
        if time.monotonic() > expires:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl: int | None = None):
        ttl = ttl if ttl is not None else self._ttl
        self._store[key] = (value, time.monotonic() + ttl)

    def clear(self):
        self._store.clear()


# ── Client ───────────────────────────────────────────────────────────────────
class ArkhamClient:
    """Async client for Arkham Intelligence REST API.

    Provides entity resolution, label lookup, portfolio history,
    with rate limiting and in-memory caching."""

    def __init__(self, cache_ttl: int = 120):
        if not ARKHAM_API_KEY:
            logger.warning("ARKHAM_API_KEY not set - ArkhamClient will return auth errors")
        self.headers = {
            "API-Key": ARKHAM_API_KEY,
            "accept": "application/json",
        }
        self.client = httpx.AsyncClient(timeout=30.0)
        self.last_call = 0.0
        self._cache = _TTLCache(default_ttl=cache_ttl)

    # ── Helpers ──────────────────────────────────────────────────────────

    async def _call(
        self,
        endpoint: str,
        params: dict | None = None,
        *,
        use_cache: bool = True,
        cache_ttl: int | None = None,
    ) -> dict:
        """Core HTTP GET with rate limiting, caching, and error handling.

        Args:
            endpoint: Path appended to BASE_URL (include leading /).
            params: Optional query parameters.
            use_cache: Whether to check/store in the TTL cache.
            cache_ttl: Override default TTL for this call.
        Returns:
            JSON response as dict, or {"error": ...} on failure.
        """
        cache_key = f"{endpoint}:{params!s}" if use_cache else None
        if cache_key:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        # Rate limit: 0.6 s between calls
        now = time.monotonic()
        wait = 0.6 - (now - self.last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        self.last_call = time.monotonic()

        url = f"{BASE_URL}{endpoint}"
        try:
            r = await self.client.get(
                url,
                headers=self.headers,
                params=params or {},
            )
            if r.status_code == 200:
                data = r.json()
                if cache_key:
                    self._cache.set(cache_key, data, ttl=cache_ttl)
                return data
            elif r.status_code == 429:
                logger.warning("Arkham rate limit hit (429)")
                return {"error": "Rate limited by Arkham API", "status": 429}
            elif r.status_code == 401:
                return {"error": "Invalid or missing API key", "status": 401}
            elif r.status_code == 404:
                return {"error": "Resource not found", "status": 404}
            else:
                return {
                    "error": f"HTTP {r.status_code}",
                    "status": r.status_code,
                    "body": r.text[:500],
                }
        except httpx.TimeoutException:
            return {"error": "Request timed out", "status": 504}
        except Exception as e:
            logger.exception("Arkham API call failed")
            return {"error": str(e)}

    # ── Public API Methods ───────────────────────────────────────────────

    async def get_entity(self, address: str) -> dict:
        """Resolve a blockchain address to a known entity.

        Returns entity name, category, and attribution metadata."""
        return await self._call(
            f"/entities/{address}",
            cache_ttl=300,  # entity resolution is fairly static
        )

    async def get_labels(self, page: int = 0, limit: int = 100) -> dict:
        """Fetch all known labels from Arkham's database.

        Returns:
            dict with 'labels' list and pagination metadata."""
        return await self._call(
            "/labels",
            params={"page": page, "limit": limit},
            cache_ttl=300,
        )

    async def get_portfolio(self, address: str) -> dict:
        """Get historical portfolio holdings for an entity/address.

        Returns:
            dict with token balances, historical snapshots, and P&L data."""
        return await self._call(
            f"/entities/{address}/portfolio",
            cache_ttl=120,  # portfolio data changes faster
        )

    async def close(self):
        """Clean up the underlying HTTP client."""
        await self.client.aclose()
