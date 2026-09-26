"""Depth of each alert queue."""

from __future__ import annotations

import os

from .constants import ALERT_QUEUES

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


async def _queue_depths() -> list[tuple[str, int]]:
    """Return (queue, depth) for each alert queue; 0 when redis is unavailable."""
    import redis.asyncio as aioredis

    from app.domains.telegram.adminbot.redis_listener import _build_redis_url

    client = aioredis.from_url(
        _build_redis_url(REDIS_URL, os.environ.get("REDIS_PASSWORD")),
        decode_responses=True, protocol=2,
    )
    try:
        return [(q, int(await client.llen(q))) for q in ALERT_QUEUES]
    except Exception:
        return [(q, 0) for q in ALERT_QUEUES]
    finally:
        await client.aclose()
