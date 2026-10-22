"""Queue names and the push helper."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

SYSTEM = "rmi:alerts:system"
SECURITY = "rmi:alerts:security"


async def push(queue: str, alert: dict[str, Any]) -> None:
    """Push one alert onto an alerts queue.

    Goes through Redis so the existing dedupe window applies; a failure here is
    the caller's to handle, because a lost alert must be visible.
    """
    import redis.asyncio as aioredis

    from app.domains.telegram.adminbot.redis_listener import _build_redis_url

    base = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    url = _build_redis_url(base, os.environ.get("REDIS_PASSWORD"))
    # RESP2: redis-py 8.x + DragonflyDB password auth otherwise fails the
    # HELLO/AUTH ordering. Same workaround the alerts listener uses.
    client = aioredis.from_url(url, decode_responses=True, protocol=2)
    try:
        payload = dict(alert)
        payload.setdefault("queuedAt", datetime.now(UTC).isoformat())
        await client.rpush(queue, json.dumps(payload))
    finally:
        await client.aclose()
