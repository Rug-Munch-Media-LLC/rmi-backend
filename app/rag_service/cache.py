"""Redis connection management for rag_service."""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.rag_service.state import _redis_pool

logger = get_logger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")


async def _get_redis() -> Any:
    """Get or create a shared Redis connection (singleton pattern to prevent leaks)."""
    global _redis_pool
    import redis.asyncio as redis

    if _redis_pool is not None:
        try:
            await _redis_pool.ping()
        except (ConnectionError, OSError):
            _redis_pool = None
    if _redis_pool is None:
        _redis_pool = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD or None,
            db=0,
            decode_responses=True,
        )
    return _redis_pool
