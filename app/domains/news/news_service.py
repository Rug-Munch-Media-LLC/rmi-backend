"""

RMI News Aggregation Service - THE Crypto News Aggregator
==========================================================
This file has been modularized into app/domains/news/news_service/ package.
This stub re-exports everything for backward compatibility.
"""
from __future__ import annotations

from app.domains.news.news_service import *

__all__ = [
    "BEARISH_WORDS",
    "BULLISH_WORDS",
    "CATEGORY_KEYWORDS",
    "REDDIT_SUBREDDITS",
    "RSS_FEEDS",
    "NewsService",
    "get_news_service",
]
