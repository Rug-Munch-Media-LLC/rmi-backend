"""
Redis connection helper for community forensics router.
"""

from __future__ import annotations

import os

import redis.asyncio as aioredis


async def get_redis() -> aioredis.Redis:
    """Get a Redis connection for community forensics."""
    return aioredis.Redis(
        host=os.getenv("REDIS_HOST", "rmi-redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD", "") or None,
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
    )
