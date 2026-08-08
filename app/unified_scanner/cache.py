"""Redis-backed result cache for wallet scans.

Uses a module-level shared async Redis connection pool instead of
creating a new TCP connection per cache operation (performance fix).
"""

from __future__ import annotations

import json
import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Shared async Redis connection pool ────────────────────────────────
_redis_pool = None


async def _get_redis():
    """Get or create a shared async Redis connection with pooling."""
    global _redis_pool
    if _redis_pool is None:
        import redis.asyncio as aioredis

        _redis_pool = aioredis.ConnectionPool.from_url(
            f"redis://{os.getenv('REDIS_HOST', 'rmi-redis')}:{os.getenv('REDIS_PORT', '6379')}",
            password=os.getenv("REDIS_PASSWORD", "") or None,
            max_connections=10,
            decode_responses=True,
            retry_on_timeout=True,
            health_check_interval=30,
        )
    return aioredis.Redis(connection_pool=_redis_pool)


async def _cache_get(key: str) -> dict[str, Any] | None:
    """Get cached wallet scan result using shared connection pool."""
    try:
        r = await _get_redis()
        cached = await r.get(f"rmi:wallet_cache:{key}")
        if cached:
            return json.loads(cached)
    except (ConnectionError, OSError, json.JSONDecodeError):
        logger.debug("wallet cache get failed", exc_info=True)
    return None


async def _cache_set(key: str, data: dict[str, Any], ttl: int = 3600) -> None:
    """Cache wallet scan result for 1 hour using shared connection pool."""
    try:
        r = await _get_redis()
        await r.setex(f"rmi:wallet_cache:{key}", ttl, json.dumps(data, default=str))
    except (ConnectionError, OSError, json.JSONDecodeError):
        logger.debug("wallet cache set failed", exc_info=True)
