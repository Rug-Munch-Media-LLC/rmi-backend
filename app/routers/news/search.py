"""News search sub-router: Qdrant news_v2 rated-article search.

GET /articles (Ground News-style filters), GET /articles/{article_id},
GET /sources, GET /sentiment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import Query

# Package-namespace indirection so patches on ``app.routers.news``
# attributes (e.g. _scroll_news_v2) stay effective.
import app.routers.news as _pkg
from app.core.errors import AppError
from app.routers.news._helpers import (
    NewsArticle,
    NewsResponse,
    _normalize_sentiment_label,
    _qdrant_match_filter,
    _rate_article_dict,
    _sort_articles_by,
    router as router,
)

# ─── Ground News-style Rated Articles ─────────────────────────────
# These endpoints serve the Qdrant news_v2 collection with Ground
# News-style rating cards (app/rag/ratings.py). Filters that live in
# Qdrant metadata (category, source, token) are pushed down; rating
# filters (sentiment, factuality, market_impact) are applied after the
# on-the-fly rating computation.


@router.get("/articles", response_model=NewsResponse)
async def get_news_articles(
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
    category: str | None = None,
    sentiment: str | None = None,  # bullish / bearish / neutral
    factuality: str | None = None,  # verified / reported / opinion / rumor
    market_impact: str | None = None,  # high / medium / low
    token: str | None = None,  # filter by token mentioned
    source: str | None = None,
    sort: str = Query("published_at", pattern="^(published_at|market_impact|sentiment)$"),
) -> NewsResponse:
    """Get rated news articles with Ground News-style filters.

    Queries Qdrant ``news_v2`` with metadata filters (category, source,
    token), computes an ``ArticleRating`` card per article on the fly,
    then applies the rating-based filters and sorts.
    """
    filters: list[dict[str, Any]] = []
    if category:
        filters.append(_qdrant_match_filter("metadata.category", category))
    if source:
        filters.append(_qdrant_match_filter("metadata.source", source))
    if token:
        filters.append(_qdrant_match_filter("metadata.tokens_mentioned", token))

    # Scroll a generous window (ratings + rating filters applied in memory)
    window = min(max(limit * 6, 120), 600)
    points = await _pkg._scroll_news_v2(filters=filters or None, limit=window)

    articles: list[dict[str, Any]] = []
    for p in points:
        a = _pkg._point_to_article(p)
        rating = _rate_article_dict(a)
        a["factuality"] = rating.get("factuality", "reported")
        a["source_reputation"] = rating.get("source_reputation", "unknown")
        a["market_impact"] = rating.get("market_impact", "low")
        a["sentiment"] = _normalize_sentiment_label(rating.get("sentiment"))
        a["sentiment_score"] = rating.get("sentiment_score")
        if not a["tokens_mentioned"]:
            a["tokens_mentioned"] = list(rating.get("tokens_mentioned") or [])
        a["rating_confidence"] = rating.get("confidence")
        articles.append(a)

    # Rating filters (computed on the fly — not Qdrant metadata)
    if sentiment:
        articles = [a for a in articles if _normalize_sentiment_label(a.get("sentiment")) == sentiment.lower()]
    if factuality:
        articles = [a for a in articles if (a.get("factuality") or "").lower() == factuality.lower()]
    if market_impact:
        articles = [a for a in articles if (a.get("market_impact") or "").lower() == market_impact.lower()]

    # Token filter fallback (tokens_mentioned metadata is sparse)
    if token:
        t = token.lower()
        articles = [
            a
            for a in articles
            if t in [str(x).lower() for x in (a.get("tokens_mentioned") or [])]
            or t in (a.get("title") or "").lower()
            or t in (a.get("summary") or "").lower()
        ]

    articles = _sort_articles_by(articles, sort)

    # Corroboration counts computed over the full filtered window
    for a in articles:
        a["corroborating_sources"] = _pkg._count_corroborating(a, articles)

    total = len(articles)
    start = (page - 1) * limit
    page_articles = articles[start : start + limit]

    categories = sorted({a.get("category") or "general" for a in articles if a.get("category")})
    breakdown: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0}
    for a in articles:
        s = _normalize_sentiment_label(a.get("sentiment"))
        breakdown[s] = breakdown.get(s, 0) + 1

    return NewsResponse(
        articles=[NewsArticle(**a) for a in page_articles],
        total=total,
        page=page,
        categories=categories,
        sentiment_breakdown=breakdown,
    )


@router.get("/articles/{article_id}", response_model=NewsArticle)
async def get_article(article_id: str) -> NewsArticle:
    """Get a single article with its full Ground News rating card."""
    filters = [_qdrant_match_filter("metadata.news_id", article_id)]
    points = await _pkg._scroll_news_v2(filters=filters, limit=10)
    if not points:
        # Fall back to point-id lookup (uuid5 news:{news_id}).
        filters = [_qdrant_match_filter("id", article_id)]
        points = await _pkg._scroll_news_v2(filters=filters, limit=10)
    if not points:
        raise AppError(f"article not found: {article_id}", status_code=404)

    a = _pkg._point_to_article(points[0])
    rating = _rate_article_dict(a)
    a["factuality"] = rating.get("factuality", "reported")
    a["source_reputation"] = rating.get("source_reputation", "unknown")
    a["market_impact"] = rating.get("market_impact", "low")
    a["sentiment"] = _normalize_sentiment_label(rating.get("sentiment"))
    a["sentiment_score"] = rating.get("sentiment_score")
    if not a["tokens_mentioned"]:
        a["tokens_mentioned"] = list(rating.get("tokens_mentioned") or [])
    a["rating_confidence"] = rating.get("confidence")
    a["corroborating_sources"] = rating.get("corroborating_sources", 0)
    return NewsArticle(**a)


@router.get("/sources", response_model=dict[str, Any], operation_id="get_news_sources")
async def get_news_sources() -> dict[str, Any]:
    """List all news sources with reputation tiers (Qdrant news_v2)."""
    points = await _pkg._scroll_news_v2(limit=600)
    sources: dict[str, dict[str, Any]] = {}
    for p in points:
        a = _pkg._point_to_article(p)
        name = a.get("source") or "unknown"
        if not name:
            continue
        entry = sources.setdefault(name, {"name": name, "article_count": 0, "reputation": "unknown"})
        entry["article_count"] += 1
        if entry["reputation"] == "unknown":
            rating = _rate_article_dict(a)
            entry["reputation"] = rating.get("source_reputation", "unknown")
    return {
        "sources": sorted(sources.values(), key=lambda x: -x["article_count"]),
        "total_sources": len(sources),
    }


@router.get("/sentiment", response_model=dict[str, Any], operation_id="get_news_sentiment")
async def get_sentiment() -> dict[str, Any]:
    """Real-time sentiment overview from current news feed.

    Returns the feed sentiment summary plus a Ground News-style
    sentiment_breakdown computed from Qdrant news_v2 ratings.
    """
    points = await _pkg._scroll_news_v2(limit=300)
    breakdown: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0}
    for p in points:
        a = _pkg._point_to_article(p)
        rating = _rate_article_dict(a)
        s = _normalize_sentiment_label(rating.get("sentiment"))
        breakdown[s] = breakdown.get(s, 0) + 1
    return {
        "sentiment": breakdown,
        "sentiment_breakdown": breakdown,
        "sample_size": len(points),
        "fetched_at": datetime.now(UTC).isoformat(),
        "trending": [a.get("title") for p in points[:5] for a in [_pkg._point_to_article(p)] if a.get("sentiment")],
    }
