"""Fail-open Redis access for the host-health incident state.
Every call degrades to a fallback: a duplicate message beats a dropped alert.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

from .health_keys import MARKER_SUFFIX

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("adminbot.notify.health_redis")


class RedisOps:
    """The five Redis calls the incident state needs, each fail-open."""

    def __init__(self, client: Any | None) -> None:
        self.client: Any = client  # duck-typed; the None guard is claim()'s

    async def set_nx(self, key: str, lease: int) -> bool:
        """Take the ownership marker, or report another worker holds it."""
        return bool(await self._attempt(lambda: self.client.set(f"{key}{MARKER_SUFFIX}", "1", nx=True, ex=lease), True))

    async def read(self, key: str) -> dict[str, str]:
        """Return the stored incident hash, or empty when absent or broken."""
        return await self._attempt(lambda: self.client.hgetall(key), {})

    async def write(self, key: str, mapping: dict[str, str], ttl: int, lease: int) -> None:
        """Persist the incident, and hold its marker for the lease window."""
        await self._attempt(lambda: self.client.hset(key, mapping=mapping), 0)
        await self._attempt(lambda: self.client.expire(key, ttl), False)
        await self._attempt(lambda: self.client.expire(f"{key}{MARKER_SUFFIX}", lease), False)

    async def release(self, key: str) -> None:
        """Drop the marker so the next incident is claimable at once."""
        await self._attempt(lambda: self.client.delete(f"{key}{MARKER_SUFFIX}"), 0)

    async def _attempt(self, call: Callable[[], Awaitable[Any]], fallback: Any) -> Any:
        """Run one Redis call, logging and degrading instead of raising."""
        try:
            return await call()
        except Exception as exc:  # fail-open: a dup beats a dropped alert
            logger.warning("health_state_call_failed error=%s", type(exc).__name__)
            return fallback
