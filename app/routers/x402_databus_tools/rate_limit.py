"""Trial tracking for x402 DataBus pay-per-call tools.

Uses Redis with the same fingerprint+wallet gating as x402_enforcement.
Bots get fingerprint-based trials (1 per tool without wallet, full with device ID).
Humans connect wallet for full trial allotment.
"""

from __future__ import annotations

import os
from typing import Any

import redis

from app.core.logging import get_logger

logger = get_logger("x402_databus_tools")

TRIAL_REDIS_PREFIX = "x402:databus_trials:"
TRIAL_WINDOW_SECONDS = 86400  # 24 hours


async def _get_trial_redis() -> Any | None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            db=0,
            decode_responses=True,
        )
        await r.ping()
        return r
    except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError):
        return None


async def check_trial(identifier: str, tool_id: str, max_trials: int) -> dict[str, Any]:
    """Check if a wallet or fingerprint has remaining trial calls for a tool.

    Supports both bot fingerprint IDs and human wallet addresses.
    Uses the same Redis namespace as enforcement for consistency.
    """
    r = await _get_trial_redis()
    if not r:
        return {"allowed": True, "remaining": max_trials, "reason": "redis_unavailable"}

    key = f"{TRIAL_REDIS_PREFIX}{identifier}:{tool_id}"
    try:
        used = await r.get(key)
        used_count = int(used) if used else 0

        if used_count >= max_trials:
            ttl = await r.ttl(key)
            return {
                "allowed": False,
                "remaining": 0,
                "reason": "trials_exhausted",
                "resets_in_seconds": ttl if ttl > 0 else TRIAL_WINDOW_SECONDS,
            }

        return {"allowed": True, "remaining": max_trials - used_count, "reason": "trial_available"}
    except Exception as e:
        logger.error(f"Trial check error: {e}")
        return {"allowed": True, "remaining": max_trials, "reason": "error"}
    finally:
        await r.close()


async def consume_trial(identifier: str, tool_id: str) -> bool:
    """Record a trial call. Returns True if recorded."""
    r = await _get_trial_redis()
    if not r:
        return False
    try:
        key = f"{TRIAL_REDIS_PREFIX}{identifier}:{tool_id}"
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.expire(key, TRIAL_WINDOW_SECONDS)
        await pipe.execute()
        return True
    except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError):
        return False
    finally:
        await r.close()
