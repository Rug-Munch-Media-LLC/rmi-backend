"""x402 exact — free tier and per-caller rate limits.

The free tier is a COUNT, not a wall: once it is exhausted the caller gets a
402 challenge (a way to pay), never a dead-end 429. 429 is reserved for
abuse — a caller hammering the endpoint far above any human or bot cadence.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

from app.core.logging import get_logger

log = get_logger(__name__)

RPM_LIMIT = int(os.getenv("X402_EXACT_RPM", "60"))
_MEMORY: dict[str, tuple[int, float]] = {}


def _redis():
    """Async Redis client, or None when unavailable."""
    try:
        import redis.asyncio as redis_lib

        return redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            decode_responses=True,
        )
    except (ImportError, ValueError):
        return None


async def free_tier(identity: str, handle: str, limit: int) -> dict[str, object]:
    """Consume one free call. Returns {allowed, used, limit}."""
    if limit <= 0:
        return {"allowed": False, "used": 0, "limit": 0}
    day = datetime.now(UTC).strftime("%Y%m%d")
    key = f"x402:exact:free:{handle}:{identity}:{day}"
    client = _redis()
    if client is not None:
        try:
            used = await client.incr(key)
            if used == 1:
                await client.expire(key, 86_400)
            return {"allowed": used <= limit, "used": int(used), "limit": limit}
        except (ConnectionError, OSError, ValueError) as exc:
            log.warning(f"x402_exact_free_tier_redis_failed: {exc}")
    used, _ = _MEMORY.get(key, (0, 0.0))
    _MEMORY[key] = (used + 1, time.time() + 86_400)
    return {"allowed": used + 1 <= limit, "used": used + 1, "limit": limit}


async def permit_cadence(identity: str) -> bool:
    """Minute-window abuse guard. False means 429."""
    window = int(time.time() // 60)
    key = f"x402:exact:rpm:{identity}:{window}"
    client = _redis()
    if client is not None:
        try:
            used = await client.incr(key)
            if used == 1:
                await client.expire(key, 120)
            return int(used) <= RPM_LIMIT
        except (ConnectionError, OSError, ValueError) as exc:
            log.warning(f"x402_exact_rpm_redis_failed: {exc}")
    used, exp = _MEMORY.get(key, (0, time.time() + 120))
    _MEMORY[key] = (used + 1, exp)
    return used + 1 <= RPM_LIMIT
