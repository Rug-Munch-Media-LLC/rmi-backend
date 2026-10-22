"""Lane selection for ``GET /api/v1/news/feed``.

One module decides which envelope /feed serves so the ranked lane, the shared
memo and the legacy fallbacks cannot drift apart. The memo is shared with
``/headlines``, which writes an unranked Redis aggregate into it, so a memo hit
is trusted only when every row carries a rank score.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from app.core.errors import AppError
from app.domains.news.feed_source import is_ranked_result, load_ranked_result
from app.routers.news import _helpers as _h
from app.routers.news._helpers import logger


def _remember(result: dict[str, Any]) -> dict[str, Any]:
    """Store a non-empty envelope in the shared memo and return it."""
    _h._NEWS_CACHE = result
    _h._CACHE_TS = datetime.now(UTC)
    return result


def _memo_is_ranked() -> bool:
    """A warm memo hit counts only when its rows carry a score."""
    if not _h._NEWS_CACHE or not _h._CACHE_TS:
        return False
    if (datetime.now(UTC) - _h._CACHE_TS) >= _h._CACHE_TTL:
        return False
    return is_ranked_result(_h._NEWS_CACHE)


async def _ranked_lane(category: str | None) -> dict[str, Any] | None:
    """Ranked DB lane — the scored envelope becomes the memo."""
    try:
        result = await load_ranked_result(limit=200, category=category)
    except Exception as e:  # fail-open: a lower lane still answers
        logger.warning(f"Ranked feed lane failed: {e}")
        return None
    return _remember(result) if result else None


async def _redis_lane() -> dict[str, Any] | None:
    """Redis aggregate written by the background refresher (unranked)."""
    try:
        from app.databus.news_provider import get_aggregated_news

        agg = await get_aggregated_news(limit=200)
    except Exception as e:
        logger.warning(f"Redis aggregate read failed: {e}")
        return None
    articles = (agg or {}).get("articles") or []
    if not articles:
        return None
    return _remember(
        {
            "status": "success",
            "articles": articles,
            "total": len(articles),
            "sources": sorted({str(a.get("source", "")) for a in articles}),
            "source_count": len({str(a.get("source", "")) for a in articles}),
            "sentiment_summary": {},
            "tiers": sorted({str(a.get("tier") or "news") for a in articles}),
        }
    )


async def _legacy_lane(include_rss: bool, include_reddit: bool, include_internal: bool) -> dict[str, Any] | None:
    """Cold-cache inline fetch, time-boxed so a slow upstream cannot stall."""
    try:
        from app.domains.news.news_service import get_news_service

        fetched = await asyncio.wait_for(
            get_news_service().fetch_all(
                limit=200,
                include_rss=include_rss,
                include_reddit=include_reddit,
                include_internal=include_internal,
            ),
            timeout=12.0,
        )
    except TimeoutError:
        logger.warning("News fetch_all timed out after 12s")
        return None
    except Exception as e:
        raise AppError(f"News aggregation failed: {e!s}") from e
    return _remember(fetched) if fetched.get("articles") else None


async def resolve_envelope(
    *,
    refresh: bool,
    category: str | None,
    include_rss: bool,
    include_reddit: bool,
    include_internal: bool,
) -> dict[str, Any]:
    """Return the feed envelope: ranked memo → ranked lane → Redis → legacy."""
    if not refresh and _memo_is_ranked():
        return _h._NEWS_CACHE
    result = await _ranked_lane(category)
    if result is None and not refresh:
        result = await _redis_lane()
    if result is None:
        try:
            result = await _legacy_lane(include_rss, include_reddit, include_internal)
        except AppError:
            if not _h._NEWS_CACHE:
                raise
            result = _h._NEWS_CACHE  # degraded: last known beats an error
    if result is None and _h._NEWS_CACHE:
        result = _h._NEWS_CACHE
    if result is None:
        result = {
            "status": "success",
            "articles": [],
            "total": 0,
            "sources": [],
            "source_count": 0,
            "sentiment_summary": {},
            "tiers": [],
            "cached": False,
            "fetched_at": datetime.now(UTC).isoformat(),
        }
    return result
