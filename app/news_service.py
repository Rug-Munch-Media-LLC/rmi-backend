"""
News Service
============
Aggregates and analyzes crypto news from multiple sources, with sentiment
analysis, impact scoring, and event detection.

Backward-compatibility shim: this module now lives in ``app.domains.news``.

Usage:
    from app.news_service import NewsService, get_news_service, fetch_all_news
"""

from __future__ import annotations

from app.domains.news.news_service import *  # noqa: F403
