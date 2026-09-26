"""The live store the publisher uses when no seam is injected.

Split out so the publisher itself stays small enough to read at a glance.
Wiring follows the alerts feed exactly -- same URL resolver, same RESP2
protocol, same fail-open posture -- and the dependency is imported inside the
call, so importing this package never opens a connection and stays safe
without redis-py installed.
"""

from __future__ import annotations

import os

from app.core.logging import get_logger

from .health_keys import KEY_PREFIX, TTL_SECONDS
from .health_state import HealthStore

logger = get_logger("adminbot.notify.health_store")

__all__ = ["KEY_PREFIX", "TTL_SECONDS", "HealthStore", "open_store"]


def open_store() -> HealthStore:
    """Return the shared Redis-backed store, or a no-op one when Redis is down.

    A no-op store keeps the plane publishing (with debounce only) rather than
    going dark, because a dropped alert is worse than a duplicate one.
    """
    try:
        import redis.asyncio as aioredis
    except ImportError:
        logger.warning("health_state_store_unavailable reason=redis_import_error")
        return HealthStore(None)
    from app.domains.telegram.adminbot.redis_listener import (
        _build_redis_url,
        _resolve_redis_password,
    )

    url = _build_redis_url(
        os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
        _resolve_redis_password(),
    )
    return HealthStore(aioredis.from_url(url, decode_responses=True, protocol=2))
