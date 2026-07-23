"""Redis singleton - single source of truth for Redis connections.

Win #14 degradation: if Redis is down, the in-process LRU cache serves
stale data. Callers check get_redis_degraded() to detect degraded mode.
"""

import os
from typing import Any, TYPE_CHECKING

from app.core.logging import get_logger

logger = get_logger(__name__)

if TYPE_CHECKING:
    import redis
    import redis.asyncio as aioredis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

_redis_client: "redis.Redis | aioredis.Redis | None" = None  # type: ignore[type-arg]
_sync_pool: "redis.ConnectionPool | None" = None
_async_pool: "aioredis.ConnectionPool | None" = None
_degraded: bool = False


def get_redis() -> "redis.Redis | None":  # type: ignore[type-arg]
    """Get Redis client with connection pooling. Single source of truth."""
    global _redis_client, _sync_pool, _degraded
    import redis

    if _sync_pool is None:
        try:
            _sync_pool = redis.ConnectionPool.from_url(
                REDIS_URL,
                max_connections=20,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            logger.info("Redis connection pool created (max 20)")
        except Exception as e:
            logger.error(f"Redis pool creation failed: {e}")
            _mark_degraded()
            return None

    try:
        _redis_client = redis.Redis(connection_pool=_sync_pool)
        _redis_client.ping()
        if _degraded:
            _mark_healthy()
        return _redis_client
    except Exception as e:
        logger.error(f"Redis client creation failed: {e}")
        _mark_degraded()
        return None


def get_redis_async() -> "aioredis.Redis | None":  # type: ignore[type-arg]
    """Get async Redis client for async operations."""
    global _redis_client, _async_pool
    import redis.asyncio as aioredis

    if _async_pool is None:
        try:
            _async_pool = aioredis.ConnectionPool.from_url(
                REDIS_URL,
                max_connections=20,
                retry_on_timeout=True,
                health_check_interval=30,
            )
            logger.info("Async Redis connection pool created (max 20)")
        except Exception as e:
            logger.error(f"Async Redis pool creation failed: {e}")
            return None

    try:
        _redis_client = aioredis.Redis(connection_pool=_async_pool)
        return _redis_client
    except Exception as e:
        logger.error(f"Async Redis client creation failed: {e}")
        return None


def close_redis() -> None:
    """Close Redis connection pool."""
    global _sync_pool, _async_pool
    if _sync_pool:
        try:
            _sync_pool.disconnect()
            logger.info("Redis connection pool closed")
        except Exception as e:
            logger.error(f"Redis pool close failed: {e}")
        _sync_pool = None
    if _async_pool:
        try:
            _async_pool.disconnect()  # type: ignore[unused-coroutine]
            logger.info("Async Redis connection pool closed")
        except Exception as e:
            logger.error(f"Async Redis pool close failed: {e}")
        _async_pool = None


def _mark_degraded() -> None:
    """Mark Redis as degraded in the global registry."""
    global _degraded
    _degraded = True
    try:
        from app.core.degradation import get_registry

        get_registry().mark_down("redis")
    except Exception:
        pass


def _mark_healthy() -> None:
    """Mark Redis as healthy in the global registry."""
    global _degraded
    _degraded = False
    try:
        from app.core.degradation import get_registry

        get_registry().mark_healthy("redis")
    except Exception:
        pass


def is_redis_degraded() -> bool:
    """Check if Redis is currently degraded."""
    return _degraded


def get_lru_cache() -> Any | None:
    """Get the in-process LRU cache for degraded operation."""
    try:
        from app.core.degradation import get_registry

        return get_registry().lru
    except ImportError:
        return None
