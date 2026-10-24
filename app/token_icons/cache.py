"""Two-tier icon cache: process L1 (no IO) + Redis L2 with TTLs.

Positive entries live 7 days, negative entries 6 hours so a token without an
icon is not re-probed on every request. Redis is optional: any failure
degrades to L1-only for 60s instead of raising into the caller — and every
degradation is logged once per operation, so a dead Redis is visible in logs
rather than silently slowing every icon lookup.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable  # noqa: TC003
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

POSITIVE_TTL = 7 * 24 * 3600
NEGATIVE_TTL = 6 * 3600
_L1_TTL = 300.0
_L1_MAX = 2048
_REDIS_RETRY_AFTER = 60.0


def _key(chain: str, token: str) -> str:
    return f"tokenicon:v1:{str(chain).strip().lower()}:{token}"


def _neg_key(chain: str, token: str) -> str:
    return f"tokenicon:neg:v1:{str(chain).strip().lower()}:{token}"


def _decode(raw: Any) -> dict[str, Any] | None:
    """Parse a cached payload; junk is a miss, not an exception."""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("icon_cache_payload_undecodable", error=type(exc).__name__)
        return None
    return payload if isinstance(payload, dict) else None


class IconCache:
    """L1 dict + Redis L2. All Redis errors are logged and swallowed (fail-open)."""

    def __init__(self) -> None:
        self._l1: dict[str, tuple[float, dict[str, Any]]] = {}
        self._redis_down_until = 0.0

    def peek(self, chain: str, token: str) -> dict[str, Any] | None:
        """L1-only lookup — safe to call from sync code and hot loops."""
        entry = self._l1.get(_key(chain, token))
        if not entry:
            return None
        expires_at, payload = entry
        if expires_at < time.monotonic():
            self._l1.pop(_key(chain, token), None)
            return None
        return payload

    def _remember(self, chain: str, token: str, payload: dict[str, Any]) -> None:
        if len(self._l1) >= _L1_MAX:
            self._l1.pop(next(iter(self._l1)), None)
        self._l1[_key(chain, token)] = (time.monotonic() + _L1_TTL, payload)

    def _client(self) -> Any | None:
        """Redis client, or None while the backoff window is open."""
        if time.monotonic() < self._redis_down_until:
            return None
        try:
            from app.core.redis import get_redis_async  # local import: keeps tests light
        except ImportError as exc:
            logger.warning("icon_cache_redis_unavailable", op="import", error=type(exc).__name__)
            self._redis_down_until = time.monotonic() + _REDIS_RETRY_AFTER
            return None
        return get_redis_async()

    async def _run(self, op: str, call: Callable[[Any], Awaitable[Any]]) -> Any | None:
        """Run one Redis op through the single degradation path (None = L1 only)."""
        client = self._client()
        if client is None:
            return None
        try:
            return await call(client)
        except Exception as exc:  # Redis is optional: degrade, never raise
            logger.warning("icon_cache_redis_unavailable", op=op, error=type(exc).__name__)
            self._redis_down_until = time.monotonic() + _REDIS_RETRY_AFTER
            return None

    async def get(self, chain: str, token: str) -> dict[str, Any] | None:
        """Return a cached positive entry (L1 first, then Redis)."""
        hit = self.peek(chain, token)
        if hit is not None:
            return {**hit, "cached": True}
        raw = await self._run("get", lambda c: c.get(_key(chain, token)))
        payload = _decode(raw) if raw else None
        if not payload or not payload.get("icon_url"):
            return None
        self._remember(chain, token, payload)
        return {**payload, "cached": True}

    async def put(self, chain: str, token: str, payload: dict[str, Any]) -> None:
        """Store a validated entry in both tiers (Redis failure is non-fatal)."""
        self._remember(chain, token, payload)
        body = json.dumps(payload)
        await self._run("set", lambda c: c.set(_key(chain, token), body, ex=POSITIVE_TTL))

    async def is_missing(self, chain: str, token: str) -> bool:
        """True while a negative-cache entry is still valid."""
        found = await self._run("exists", lambda c: c.exists(_neg_key(chain, token)))
        return bool(found)

    async def mark_missing(self, chain: str, token: str) -> None:
        """Negative-cache a token so upstreams are not re-probed for 6h."""
        await self._run(
            "set_negative",
            lambda c: c.set(_neg_key(chain, token), "1", ex=NEGATIVE_TTL),
        )


_CACHE = IconCache()


def get_icon_cache() -> IconCache:
    """Process-wide icon cache singleton."""
    return _CACHE


def peek_icon_url(chain: str, token: str) -> str | None:
    """L1-only icon URL (used by trending to avoid any upstream latency)."""
    entry = _CACHE.peek(chain, token)
    return str(entry["icon_url"]) if entry and entry.get("icon_url") else None
