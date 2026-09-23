"""Process-wide TTL cache for NewsService.fetch_all.

The scanner's event-correlation live fallback called ``fetch_all`` (100+ RSS
feeds + coingecko/cryptopanic/ghost/scanner/alert sources) once PER TOKEN
SCAN — ~2 min of network per token, which is why the scan queue drained at
~1 token/min (2026-09-23). This wrapper serves one shared result for
``NEWS_FETCH_CACHE_TTL_S`` seconds (default 240) so concurrent scans, and
consecutive scans in the same window, reuse a single fetch.

Only the scan fallback path uses this wrapper; the public news API keeps
calling ``NewsService.fetch_all`` directly and stays uncached.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from app.core.logging_config import setup_stdlib_logging
from app.domains.news.news_service.service import get_news_service

logger = setup_stdlib_logging(__name__)

CACHE_TTL_S = float(os.getenv("NEWS_FETCH_CACHE_TTL_S", "240"))

_lock = asyncio.Lock()
_cached_result: dict[str, Any] | None = None
_cached_at = 0.0


async def fetch_all_cached(**kwargs: Any) -> dict[str, Any]:
    """``NewsService.fetch_all`` behind a single-flight TTL cache."""
    global _cached_result, _cached_at

    if _cached_result is not None and (time.monotonic() - _cached_at) < CACHE_TTL_S:
        return _cached_result

    async with _lock:
        if _cached_result is not None and (time.monotonic() - _cached_at) < CACHE_TTL_S:
            return _cached_result

        result = await get_news_service().fetch_all(**kwargs)
        _cached_result = result
        _cached_at = time.monotonic()
        logger.info("news_fetch_cached", extra={"ttl_s": CACHE_TTL_S})
        return result


def clear_cache() -> None:
    """Reset the cache (tests / ops)."""
    global _cached_result, _cached_at
    _cached_result = None
    _cached_at = 0.0
