"""Cross-worker incident state for the host-health plane.

Module-level globals meant one transition published once per gunicorn worker;
state now lives in Redis, so the decision is global and survives a restart.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from .health_keys import KEY_PREFIX, LEASE_SECONDS, TTL_SECONDS
from .health_redis import RedisOps
from .incident import Incident

if TYPE_CHECKING:
    from collections.abc import Callable

FINGERPRINT = "host_health"  # one host, one incident: the plane itself


class HealthStore:
    """Redis-backed incident state, degrading to no suppression on failure."""

    def __init__(self, client: Any | None, tick: Callable[[], float] = time.time) -> None:
        self._ops = RedisOps(client)
        self._key = f"{KEY_PREFIX}{FINGERPRINT}"
        self._tick = tick

    async def now(self) -> float:
        """Return the bound wall clock, so the windows survive a restart."""
        return self._tick()

    async def claim(self) -> bool:
        """Own the incident; False when another worker already holds it."""
        if self._ops.client is None:
            return True  # no Redis: fail open rather than silence the plane
        return await self._ops.set_nx(self._key, LEASE_SECONDS)

    async def load(self, now: float) -> Incident:
        """Return the stored incident, or a never-seen one for a first sample."""
        fresh = Incident(now, 0.0, 0.0, now)
        return Incident.decode(await self._ops.read(self._key)) or fresh

    async def save(self, incident: Incident, status: str) -> None:
        """Persist the incident, and free the marker once it has resolved."""
        await self._ops.write(self._key, incident.encode(status), TTL_SECONDS, LEASE_SECONDS)
        if status == "resolved":
            await self._ops.release(self._key)
