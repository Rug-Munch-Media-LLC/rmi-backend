"""Async Redis access for RAG index bookkeeping.

Relocated from ``app.rag.compat._get_redis`` (Track 1 RAG cutaway). The
backend keeps a single lazily-created async Redis pool for reading the
``rag:idx:*`` index keys written by the ingestion pipeline.
"""

from __future__ import annotations

import os
from typing import Any

_redis_pool: Any = None


async def get_redis() -> Any:
    """Get the shared async Redis connection (lazily created)."""
    import redis.asyncio as aioredis

    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            os.getenv("REDIS_URL", "redis://rmi-redis:6379/0"),
            decode_responses=True,
        )
    return _redis_pool
