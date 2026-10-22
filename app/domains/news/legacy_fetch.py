"""Last-resort upstream news fetch for the aggregate feed.

Extracted from ``app/routers/news/feed.py`` so the router does no inline
network I/O. Only reached when both the in-process memo, the Redis aggregate
and the ranked ``news_items`` lane are empty (e.g. a cold deploy), and it is
time-boxed so the request can never hang on slow publishers.

Fail-open contract: any failure returns the caller's last-known good cache
(``fallback``) or ``None`` — and ``None`` means "serve an explicit empty",
never a 500.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger("news_legacy_fetch")

FETCH_TIMEOUT_SECONDS = 12.0


async def fetch_legacy_aggregate(
    *,
    fallback: dict[str, Any] | None = None,
    include_rss: bool = True,
    include_reddit: bool = True,
    include_internal: bool = True,
) -> dict[str, Any] | None:
    """Time-boxed upstream aggregation; returns a feed envelope or the fallback."""
    try:
        from app.domains.news.news_service import get_news_service

        fetched = await asyncio.wait_for(
            get_news_service().fetch_all(
                limit=200,
                include_rss=include_rss,
                include_reddit=include_reddit,
                include_internal=include_internal,
            ),
            timeout=FETCH_TIMEOUT_SECONDS,
        )
        if fetched.get("articles"):
            return fetched
        return fallback
    except TimeoutError:
        logger.warning("News fetch_all timed out after %ss", FETCH_TIMEOUT_SECONDS)
        return fallback
    except Exception as exc:
        logger.error("News fetch failed: %s", exc)
        if fallback:
            return fallback
        raise AppError(f"News aggregation failed: {exc!s}") from exc


def stamp_cache_ts() -> datetime:
    """UTC timestamp for cache bookkeeping (keeps call sites terse)."""
    return datetime.now(UTC)
