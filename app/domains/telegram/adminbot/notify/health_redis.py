"""Fail-open Redis access for the host-health incident state.

Every call degrades to a fallback instead of raising: a Redis problem must
never silence the alert plane, so a duplicate message is the accepted cost.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger("adminbot.notify.health_redis")

# Generous ceiling so a key can never outlive a week of quiet alerts.
KEY_PREFIX = os.getenv("HEALTH_ALERT_STATE_PREFIX", "rmi:health:incident:")
TTL_SECONDS = int(os.getenv("HEALTH_ALERT_STATE_TTL", "604800"))


class RedisOps:
    """The four Redis calls the incident state needs, each fail-open."""

    def __init__(self, client: Any | None) -> None:
        self.client = client

    async def set_nx(self, key: str, ttl: int) -> bool:
        """Create the ownership marker, or report another worker holds it."""
        return bool(await self._attempt(lambda: self.client.set(f"{key}:seen", "1", nx=True, ex=ttl), True))

    async def read(self, key: str) -> dict[str, str]:
        """Return the stored incident hash, or empty when absent or broken."""
        return await self._attempt(lambda: self.client.hgetall(key), {})

    async def write(self, key: str, mapping: dict[str, str], ttl: int) -> None:
        """Persist the incident and refresh its expiry."""
        await self._attempt(lambda: self.client.hset(key, mapping=mapping), 0)
        await self._attempt(lambda: self.client.expire(key, ttl), False)

    async def _attempt(self, call: Callable[[], Awaitable[Any]], fallback: Any) -> Any:
        """Run one Redis call, logging and degrading instead of raising."""
        try:
            return await call()
        except Exception as exc:
            logger.warning("health_state_call_failed error=%s", type(exc).__name__)
            return fallback
