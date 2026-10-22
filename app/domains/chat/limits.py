"""Per-user fixed-window rate limit for the chat endpoint.

Redis-backed with an in-memory fallback so a Redis outage degrades the
limit instead of failing the request. Identities are salted-hashed before
touching Redis (no PII in keys or logs).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

WINDOW_S = 60
DEFAULT_PER_MINUTE = 10


def _identity_hash(identity: str) -> str:
    salt = os.getenv("RATE_LIMIT_SALT", "chat-canonical")
    return hashlib.sha256(f"{salt}:{identity}".encode("utf-8", "ignore")).hexdigest()[:24]


class UserRateLimiter:
    """Allow ``per_minute`` requests per rolling fixed window per identity."""

    def __init__(self, per_minute: int | None = None) -> None:
        self.per_minute = max(
            1, int(os.getenv("CHAT_RATE_PER_MINUTE", str(per_minute or DEFAULT_PER_MINUTE)))
        )
        self._redis: Any = None
        self._redis_failed = False
        self._memory: dict[str, tuple[float, int]] = {}
        self._lock = asyncio.Lock()

    async def _client(self) -> Any:
        if self._redis is not None:
            return self._redis
        if self._redis_failed:
            return None
        try:
            from app.core.redis import get_redis_async

            client = get_redis_async()
            if client is None:
                raise RuntimeError("redis unavailable")
            await client.ping()
            self._redis = client
            return client
        except Exception as exc:
            self._redis_failed = True
            log.warning("chat_rate_limit_redis_down err=%s", type(exc).__name__)
            return None

    def _allow_memory(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        window_start, count = self._memory.get(key, (now, 0))
        if now - window_start >= WINDOW_S:
            window_start, count = now, 0
        count += 1
        self._memory[key] = (window_start, count)
        retry_after = max(1, int(WINDOW_S - (now - window_start)))
        return count <= self.per_minute, retry_after

    async def allow(self, identity: str) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)`` for ``identity``."""
        key = _identity_hash(identity or "anonymous")
        client = await self._client()
        if client is None:
            async with self._lock:
                return self._allow_memory(key)
        redis_key = f"rmi:chat:canonical:rl:{key}"
        try:
            count = await client.incr(redis_key)
            if count == 1:
                await client.expire(redis_key, WINDOW_S)
            ttl = await client.ttl(redis_key)
            return count <= self.per_minute, max(1, int(ttl if ttl and ttl > 0 else WINDOW_S))
        except Exception as exc:
            self._redis = None
            self._redis_failed = True
            log.warning("chat_rate_limit_incr_failed err=%s", type(exc).__name__)
            async with self._lock:
                return self._allow_memory(key)
