"""Redis-backed result cache for wallet scans."""

import hashlib
import json
import os

from app.core.logging import get_logger

logger = get_logger(__name__)


async def _cache_get(key: str) -> dict | None:
    """Get cached wallet scan result."""
    try:
        import redis.asyncio as aioredis

        r = await aioredis.from_url(
            f"redis://{os.getenv('REDIS_HOST', 'rmi-redis')}:{os.getenv('REDIS_PORT', '6379')}",
            password=os.getenv("REDIS_PASSWORD", "") or None,
        )
        cached = await r.get(f"rmi:wallet_cache:{key}")
        await r.close()
        if cached:
            return json.loads(cached)
    except Exception:
        logger.debug("wallet cache get failed", exc_info=True)
    return None


async def _cache_set(key: str, data: dict, ttl: int = 3600) -> None:
    """Cache wallet scan result for 1 hour."""
    try:
        import json
        import os

        import redis.asyncio as aioredis

        r = await aioredis.from_url(
            f"redis://{os.getenv('REDIS_HOST', 'rmi-redis')}:{os.getenv('REDIS_PORT', '6379')}",
            password=os.getenv("REDIS_PASSWORD", "") or None,
        )
        await r.setex(f"rmi:wallet_cache:{key}", ttl, json.dumps(data))
        await r.close()
    except Exception:
        logger.debug("wallet cache set failed", exc_info=True)
