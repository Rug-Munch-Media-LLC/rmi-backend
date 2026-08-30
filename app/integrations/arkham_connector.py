"""
Arkham Intelligence API Connector
Entity labeling, wallet attribution, institutional tracking, sanctions screening.
Base URL: https://api.arkhamintelligence.com
Auth header: API-Key (from secrets manager or ARKHAM_API_KEY env var)
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from app.core.circuit_breaker import AsyncCircuitBreaker, CircuitBreakerOpenError
from app.core.logging import get_logger

logger = get_logger(__name__)

# Circuit breaker for Arkham API
_arkham_cb = AsyncCircuitBreaker(
    name="arkham",
    failure_threshold=5,
    failure_window=60.0,
    recovery_timeout=30.0,
)

# ── Auth ────────────────────────────────────────────────────────────────────
ARKHAM_API_KEY = os.getenv("ARKHAM_API_KEY", "").strip()
if not ARKHAM_API_KEY:
    # Fallback to reading from secrets file
    _secrets_paths = ["[SECRETS_PATH]/arkham_api_key"]
    for _sp in _secrets_paths:
        if os.path.exists(_sp):
            with open(_sp) as _f:
                ARKHAM_API_KEY = _f.read().strip()
            break

BASE_URL = "https://api.arkhamintelligence.com"


# ── Simple TTL Cache ────────────────────────────────────────────────────────
class _TTLCache:
    """In-memory cache with per-key TTL for rate-limited API responses."""

    def __init__(self, default_ttl: int = 120) -> None:
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

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        ttl = ttl if ttl is not None else self._ttl
        self._store[key] = (value, time.monotonic() + ttl)

    def clear(self) -> None:
        self._store.clear()


# ── Client ───────────────────────────────────────────────────────────────────
class ArkhamClient:
    """Async client for Arkham Intelligence REST API.

    Provides entity resolution, label lookup, portfolio history,
    with rate limiting and in-memory caching."""

    def __init__(self, cache_ttl: int = 120) -> None:
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
        params: dict[str, Any] | None = None,
        *,
        use_cache: bool = True,
        cache_ttl: int | None = None,
    ) -> dict[str, Any]:
        """Core HTTP GET with rate limiting, caching, error handling, and circuit breaker.

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

        # Circuit breaker check — serve stale cache if OPEN
        if not await _arkham_cb.can_call():
            stale_cache = self._cache.get(cache_key) if cache_key else None
            if stale_cache is not None:
                return stale_cache
            stale_cb = _arkham_cb.get_stale_result()
            if stale_cb is not None:
                return stale_cb
            raise CircuitBreakerOpenError(
                _arkham_cb.name,
                _arkham_cb.state,
                "Arkham circuit OPEN — failing fast",
            )

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
                _arkham_cb.set_stale_result(data)
                await _arkham_cb.record_success()
                return data
            elif r.status_code == 429:
                logger.warning("Arkham rate limit hit (429)")
                await _arkham_cb.record_failure(is_server_error=False)
                return {"error": "Rate limited by Arkham API", "status": 429}
            elif r.status_code == 401:
                return {"error": "Invalid or missing API key", "status": 401}
            elif r.status_code == 404:
                return {"error": "Resource not found", "status": 404}
            else:
                is_5xx = r.status_code >= 500
                await _arkham_cb.record_failure(is_server_error=is_5xx)
                return {
                    "error": f"HTTP {r.status_code}",
                    "status": r.status_code,
                    "body": r.text[:500],
                }
        except httpx.TimeoutException:
            await _arkham_cb.record_failure(is_server_error=True)
            return {"error": "Request timed out", "status": 504}
        except Exception as e:
            logger.exception("Arkham API call failed")
            await _arkham_cb.record_failure(is_server_error=True)
            return {"error": str(e)}

    # ── Public API Methods ───────────────────────────────────────────────

    async def get_entity(self, address: str) -> dict[str, Any]:
        """Resolve a blockchain address to a known entity.

        Returns entity name, category, and attribution metadata."""
        return await self._call(
            f"/entities/{address}",
            cache_ttl=300,  # entity resolution is fairly static
        )

    async def get_labels(self, page: int = 0, limit: int = 100) -> dict[str, Any]:
        """Fetch all known labels from Arkham's database.

        Returns:
            dict with 'labels' list and pagination metadata."""
        return await self._call(
            "/labels",
            params={"page": page, "limit": limit},
            cache_ttl=300,
        )

    async def get_portfolio(self, address: str) -> dict[str, Any]:
        """Get historical portfolio holdings for an entity/address.

        Returns:
            dict with token balances, historical snapshots, and P&L data."""
        return await self._call(
            f"/entities/{address}/portfolio",
            cache_ttl=120,  # portfolio data changes faster
        )

    async def close(self) -> None:
        """Clean up the underlying HTTP client."""
        await self.client.aclose()
