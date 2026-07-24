"""
RMI News Aggregation Service — modular package.
Re-exports all public symbols for backward compatibility.
"""
from __future__ import annotations

from app.domains.news.news_service.service import NewsService, get_news_service
from app.domains.news.news_service.sources import (
    BEARISH_WORDS,
    BULLISH_WORDS,
    CATEGORY_KEYWORDS,
    REDDIT_SUBREDDITS,
    RSS_FEEDS,
)


# Convenience: standalone fetch all news (for databus consumers)
async def fetch_all_news(
    limit: int = 100,
    include_rss: bool = True,
    include_reddit: bool = True,
    include_api: bool = True,
    include_internal: bool = True,
) -> dict:
    """Convenience function: fetch all news from all sources."""
    svc = get_news_service()
    return await svc.fetch_all(
        limit=limit,
        include_rss=include_rss,
        include_reddit=include_reddit,
        include_api=include_api,
        include_internal=include_internal,
    )

__all__ = [
    "BEARISH_WORDS",
    "BULLISH_WORDS",
    "CATEGORY_KEYWORDS",
    "REDDIT_SUBREDDITS",
    "RSS_FEEDS",
    "NewsService",
    "fetch_all_news",
    "get_news_service",
]
