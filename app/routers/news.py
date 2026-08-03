"""

News Router - THE Crypto News Aggregator Feed
=============================================
200+ sources. Real data only. No fake articles.

Endpoints:
  GET /api/v1/news/feed       - Full aggregated feed with filters
  GET /api/v1/news/articles   - Ground News-style rated articles (Qdrant news_v2)
  GET /api/v1/news/articles/:article_id - Single article with full rating card
  GET /api/v1/news/sources    - Active sources list with counts + reputation tiers
  GET /api/v1/news/sentiment  - Real-time sentiment overview + sentiment_breakdown
  GET /api/v1/news/trending   - Trending crypto topics from recent news (Qdrant)
  GET /api/v1/news/headlines  - Top headlines only
  GET /api/v1/news/stats      - Source/category statistics
  POST /api/v1/news/comment   - Comment on article (social)
  GET /api/v1/news/comments/:article_id - Get comments for article
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.auth import require_public_profile
from app.core.errors import AppError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/news", tags=["news"])


# ─── Models ───────────────────────────────────────────────────────
class NewsArticle(BaseModel):
    id: str | None = None
    title: str = ""
    url: str = ""
    description: str | None = None
    source: str = ""
    published_at: str | None = None
    image_url: str | None = None
    category: str | None = None
    sentiment: str | None = None
    kind: str | None = None
    tier: str | None = None
    reddit_score: int | None = None
    reddit_comments: int | None = None
    risk_score: float | None = None
    token_address: str | None = None
    chain: str | None = None
    comment_count: int | None = 0
    # Ground News-style ratings (enriched by app.domains.news.ratings)
    bias: str | None = None
    sentiment_score: float | None = None
    sentiment_label: str | None = None
    factual_rating: str | None = None
    rating_confidence: float | None = None
    source_tier: int | None = None
    # Ground News-style rating card (app/rag/ratings.py ArticleRating)
    summary: str | None = None
    factuality: str | None = None  # verified / reported / opinion / rumor
    source_reputation: str | None = None  # established / independent / unknown
    market_impact: str | None = None  # high / medium / low
    tokens_mentioned: list[str] = Field(default_factory=list)
    group_id: str | None = None  # same story from multiple sources
    corroborating_sources: int = 0  # how many sources report this


class NewsResponse(BaseModel):
    """Ground News-style rated article listing (Qdrant news_v2)."""

    articles: list[NewsArticle] = []
    total: int = 0
    page: int = 1
    categories: list[str] = []
    sentiment_breakdown: dict[str, int] = {}


class NewsFeedResponse(BaseModel):
    status: str = "success"
    articles: list[NewsArticle] = []
    total: int = 0
    sources: list[str] = []
    source_count: int = 0
    sentiment_summary: dict[str, int] = {}
    tiers: list[str] = []
    cached: bool = False
    fetched_at: str = ""


class CommentRequest(BaseModel):
    article_id: str
    author: str = "anon"
    content: str
    parent_id: str | None = None


class CommentResponse(BaseModel):
    id: str
    article_id: str
    author: str
    content: str
    created_at: str
    parent_id: str | None = None
    likes: int = 0


# ─── In-memory cache ──────────────────────────────────────────────
_NEWS_CACHE = []
_CACHE_TS: datetime | None = None
_CACHE_TTL = timedelta(minutes=3)

# In-memory comments store (ephemeral - persists via Redis later)
_COMMENTS: dict[str, list[dict]] = {}

# ─── Helpers ──────────────────────────────────────────────────────


def _make_comment_id(article_id: str, content: str) -> str:
    h = hashlib.sha256(f"{article_id}:{content}:{datetime.now(UTC).isoformat()}".encode()).hexdigest()
    return f"comment-{h[:12]}"


async def _refresh_cache(include_rss: bool, include_reddit: bool, include_internal: bool) -> None:
    """Background cache refresh task."""
    global _NEWS_CACHE, _CACHE_TS
    try:
        from app.domains.news.news_service import get_news_service

        svc = get_news_service()
        result = await svc.fetch_all(
            limit=200,
            include_rss=include_rss,
            include_reddit=include_reddit,
            include_internal=include_internal,
        )
        _NEWS_CACHE = result
        _CACHE_TS = datetime.now(UTC)
        logger.info(
            f"News cache refreshed: {result.get('total', 0)} articles from {result.get('source_count', 0)} sources"
        )
    except Exception as e:
        logger.warning(f"Background news refresh failed: {e}")


# ─── Ground News-style rated articles (Qdrant news_v2) ─────────────
# The rated-article API is backed by the RAG `news_v2` collection
# (seeded by rmi-rag/scripts/news_to_rag.py). Each point payload:
#   payload.content                    → article text
#   payload.metadata.{title,source,url,published_at,category,
#                     tokens_mentioned,chains_mentioned,news_id}
# Ratings are computed on the fly via app/rag/ratings.py so old
# articles (ingested before the rating engine existed) get a card too.

_NEWS_QDRANT_COLLECTION = "news_v2"
_NEWS_SCROLL_WINDOW = 100  # Qdrant scroll batch size


def _news_qdrant_url() -> str:
    """Resolve the Qdrant base URL (docker service name vs localhost)."""
    from app.core.health_route import QDRANT_URL

    return QDRANT_URL.rstrip("/")


def _point_to_article(point: dict[str, Any]) -> dict[str, Any]:
    """Convert a Qdrant news_v2 point payload into a flat article dict."""
    payload = point.get("payload") or {}
    meta = payload.get("metadata") or {}
    content = payload.get("content") or ""
    return {
        "id": meta.get("news_id") or str(point.get("id", "")),
        "title": meta.get("title") or "",
        "url": meta.get("url") or "",
        "source": meta.get("source") or payload.get("source") or "",
        "published_at": meta.get("published_at") or meta.get("date") or "",
        "summary": (content or "")[:500],
        "description": (content or "")[:500],
        "category": meta.get("category") or "",
        "tokens_mentioned": list(meta.get("tokens_mentioned") or []),
        "chains_mentioned": list(meta.get("chains_mentioned") or []),
        "news_id": meta.get("news_id") or "",
        "group_id": meta.get("group_id"),
        "point_id": str(point.get("id", "")),
    }


def _qdrant_match_filter(field: str, value: str) -> dict[str, Any]:
    """Qdrant exact-match condition for a metadata field."""
    return {"key": field, "match": {"value": value}}


async def _scroll_news_v2(
    *,
    filters: list[dict[str, Any]] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Scroll Qdrant `news_v2` points with optional metadata filters.

    Returns raw point dicts (id + payload). Qdrant scroll returns points
    in storage order; callers sort by ``published_at`` afterwards.
    """
    import httpx

    base = _news_qdrant_url()
    try:
        async with httpx.AsyncClient(base_url=base, timeout=10.0) as client:
            body: dict[str, Any] = {"limit": min(limit, 1000), "with_payload": True}
            if filters:
                body["filter"] = {"must": filters}
            r = await client.post(f"/collections/{_NEWS_QDRANT_COLLECTION}/points/scroll", json=body)
            if r.status_code != 200:
                logger.warning("news_qdrant_scroll_failed status=%d body=%s", r.status_code, r.text[:200])
                return []
            return r.json().get("result", {}).get("points", []) or []
    except (httpx.HTTPError, httpx.TimeoutException, OSError, ValueError) as e:
        logger.debug("news_qdrant_scroll_error err=%s", e)
        return []


def _rate_article_dict(article: dict[str, Any]) -> dict[str, Any]:
    """Compute the Ground News rating card for an article dict.

    Uses app/rag/ratings.py rate_article(). Never raises — malformed
    articles get a conservative default card.
    """
    try:
        from app.rag.ratings import rate_article

        rating = rate_article(article)
        return rating.to_dict()
    except Exception as e:  # fail-open rating
        logger.debug("news_rating_failed err=%s", e)
        return {
            "factuality": "reported",
            "source_reputation": "unknown",
            "market_impact": "low",
            "sentiment": "NEUTRAL",
            "tokens_mentioned": [],
            "protocols_mentioned": [],
            "people_mentioned": [],
            "corroborating_sources": 0,
            "confidence": 0.3,
        }


def _normalize_sentiment_label(label: str | None) -> str:
    """Map rating sentiment (BULLISH/BEARISH/NEUTRAL) to API values."""
    if not label:
        return "neutral"
    return label.strip().lower()


def _sort_articles_by(
    articles: list[dict[str, Any]],
    sort: str,
) -> list[dict[str, Any]]:
    """Sort articles by the requested sort key (stable).

    published_at  → newest first (ISO-8601 string compare is safe here)
    market_impact → high > medium > low
    sentiment     → bullish > bearish > neutral
    """
    impact_rank = {"high": 0, "medium": 1, "low": 2, "": 3}
    sentiment_rank = {"bullish": 0, "bearish": 1, "neutral": 2, "": 3}

    def _key(a: dict[str, Any]) -> tuple[Any, ...]:
        if sort == "market_impact":
            return (impact_rank.get((a.get("market_impact") or "").lower(), 3),)
        if sort == "sentiment":
            return (sentiment_rank.get(_normalize_sentiment_label(a.get("sentiment")), 3),)
        return (a.get("published_at") or "",)

    articles_sorted = sorted(articles, key=_key)
    if sort != "sentiment":
        # published_at / market_impact: descending (impact rank 0 = high first,
        # but for published_at we want newest first — handled by key reversal).
        articles_sorted.reverse()
    return articles_sorted


def _count_corroborating(article: dict[str, Any], articles: list[dict[str, Any]]) -> int:
    """Count how many distinct sources report the same story as *article*.

    Uses the Qdrant ``group_id`` when present; otherwise groups by a
    normalised title hash so same-story variants from different sources
    corroborate each other.
    """
    group = article.get("group_id") or ""
    if not group and article.get("title"):
        norm = "".join(ch for ch in article["title"].lower() if ch.isalnum())[:80]
        group = f"title:{norm}"
    if not group:
        return 0
    sources: set[str] = set()
    for a in articles:
        a_group = a.get("group_id") or ""
        if not a_group and a.get("title"):
            a_norm = "".join(ch for ch in a["title"].lower() if ch.isalnum())[:80]
            a_group = f"title:{a_norm}"
        if a_group and a_group == group and a.get("source"):
            sources.add(str(a["source"]))
    # exclude the article itself (same source) from the corroboration count
    return max(len(sources) - 1, 0)


# ─── Main Feed ────────────────────────────────────────────────────


@router.get("/feed", response_model=NewsFeedResponse)
async def get_news_feed(
    limit: int = Query(50, ge=1, le=200),
    sentiment: str | None = None,
    source: str | None = None,
    category: str | None = None,
    tier: str | None = None,
    kind: str | None = None,
    include_rss: bool = Query(True),
    include_reddit: bool = Query(True),
    include_internal: bool = Query(True),
    refresh: bool = Query(False, description="Force refresh, bypass cache"),
) -> Any:
    """Full aggregated news feed from all sources."""
    global _NEWS_CACHE, _CACHE_TS

    # Return cache if fresh
    if not refresh and _NEWS_CACHE and _CACHE_TS and (datetime.now(UTC) - _CACHE_TS) < _CACHE_TTL:
        result = _NEWS_CACHE
    else:
        # Serve stale cache immediately while refreshing in background
        if _NEWS_CACHE and not refresh:
            result = _NEWS_CACHE
            # Background refresh
            asyncio.create_task(_refresh_cache(include_rss, include_reddit, include_internal))
        else:
            # First load or forced refresh - fetch inline but with limits
            try:
                from app.domains.news.news_service import get_news_service

                svc = get_news_service()
                result = await svc.fetch_all(
                    limit=200,
                    include_rss=include_rss,
                    include_reddit=include_reddit,
                    include_internal=include_internal,
                )
                _NEWS_CACHE = result
                _CACHE_TS = datetime.now(UTC)
            except Exception as e:
                logger.error(f"News fetch failed: {e}")
                if _NEWS_CACHE:
                    result = _NEWS_CACHE
                else:
                    raise AppError(f"News aggregation failed: {e!s}") from e

    articles = result.get("articles", [])

    # Apply filters
    if sentiment:
        articles = [a for a in articles if a.get("sentiment", "").lower() == sentiment.lower()]
    if source:
        articles = [a for a in articles if source.lower() in a.get("source", "").lower()]
    if category:
        articles = [a for a in articles if a.get("category", "").lower() == category.lower()]
    if tier:
        articles = [a for a in articles if a.get("tier", "").lower() == tier.lower()]
    if kind:
        articles = [a for a in articles if a.get("kind", "").lower() == kind.lower()]

    articles = articles[:limit]

    # Enrich with Ground News-style ratings (bias, sentiment_score, factual_rating)
    try:
        from app.domains.news.ratings import enrich_articles

        articles = enrich_articles(articles)
    except Exception as e:
        logger.warning(f"News rating enrichment failed: {e}")

    # Enrich with comment counts
    for a in articles:
        aid = a.get("id", "")
        a["comment_count"] = len(_COMMENTS.get(aid, []))

    return NewsFeedResponse(
        status="success",
        articles=[NewsArticle(**a) for a in articles],
        total=len(articles),
        sources=result.get("sources", []),
        source_count=result.get("source_count", 0),
        sentiment_summary=result.get("sentiment_summary", {}),
        tiers=result.get("tiers", []),
        cached=bool(_NEWS_CACHE),
        fetched_at=(_CACHE_TS or datetime.now(UTC)).isoformat(),
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
    points = await _scroll_news_v2(filters=filters or None, limit=window)

    articles: list[dict[str, Any]] = []
    for p in points:
        a = _point_to_article(p)
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
        a["corroborating_sources"] = _count_corroborating(a, articles)

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
    points = await _scroll_news_v2(filters=filters, limit=10)
    if not points:
        # Fall back to point-id lookup (uuid5 news:{news_id}).
        filters = [_qdrant_match_filter("id", article_id)]
        points = await _scroll_news_v2(filters=filters, limit=10)
    if not points:
        raise AppError(f"article not found: {article_id}", status_code=404)

    a = _point_to_article(points[0])
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


@router.get("/sources", response_model=dict[str, Any])
async def get_news_sources() -> dict[str, Any]:
    """List all news sources with reputation tiers (Qdrant news_v2)."""
    points = await _scroll_news_v2(limit=600)
    sources: dict[str, dict[str, Any]] = {}
    for p in points:
        a = _point_to_article(p)
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


@router.get("/sentiment", response_model=dict[str, Any])
async def get_sentiment() -> dict[str, Any]:
    """Real-time sentiment overview from current news feed.

    Returns the feed sentiment summary plus a Ground News-style
    sentiment_breakdown computed from Qdrant news_v2 ratings.
    """
    points = await _scroll_news_v2(limit=300)
    breakdown: dict[str, int] = {"bullish": 0, "bearish": 0, "neutral": 0}
    for p in points:
        a = _point_to_article(p)
        rating = _rate_article_dict(a)
        s = _normalize_sentiment_label(rating.get("sentiment"))
        breakdown[s] = breakdown.get(s, 0) + 1
    return {
        "sentiment": breakdown,
        "sentiment_breakdown": breakdown,
        "sample_size": len(points),
        "fetched_at": datetime.now(UTC).isoformat(),
        "trending": [a.get("title") for p in points[:5] for a in [_point_to_article(p)] if a.get("sentiment")],
    }


# ─── Headlines ────────────────────────────────────────────────────


@router.get("/headlines")
async def get_headlines(
    count: int = Query(10, ge=1, le=50),
    category: str | None = Query(None),
) -> dict[str, list[dict]]:
    """Top headlines only - fast, lightweight."""
    feed = await get_news_feed(limit=count, category=category, include_reddit=False)
    return {
        "headlines": [
            {
                "title": a.title,
                "url": a.url,
                "source": a.source,
                "published_at": a.published_at,
                "category": a.category,
                "sentiment": a.sentiment,
                "kind": a.kind,
            }
            for a in feed.articles[:count]
        ],
        "count": len(feed.articles[:count]),
    }


# ─── Categories ───────────────────────────────────────────────────


@router.get("/categories")
async def get_categories() -> dict[str, list[dict]]:
    """Get all news categories with counts."""
    feed = await get_news_feed(limit=200, refresh=False)
    cats = {}
    for a in feed.articles:
        cat = a.category or "general"
        cats[cat] = cats.get(cat, 0) + 1
    return {
        "categories": [{"name": n, "count": c} for n, c in sorted(cats.items(), key=lambda x: -x[1])],
    }


# ─── Stats ────────────────────────────────────────────────────────


@router.get("/stats")
async def get_stats() -> dict:
    """Aggregate statistics about the news pipeline."""
    feed = await get_news_feed(limit=200, refresh=False)
    source_counts = {}
    tier_counts = {}
    for a in feed.articles:
        src = a.source or "unknown"
        source_counts[src] = source_counts.get(src, 0) + 1
        t = a.tier or "unknown"
        tier_counts[t] = tier_counts.get(t, 0) + 1

    return {
        "total_articles": feed.total,
        "total_sources": feed.source_count,
        "sentiment": feed.sentiment_summary,
        "source_breakdown": dict(sorted(source_counts.items(), key=lambda x: -x[1])[:20]),
        "tier_breakdown": tier_counts,
        "fetched_at": feed.fetched_at,
    }


# ─── Comments / Social ────────────────────────────────────────────


@router.post("/comment", response_model=CommentResponse)
async def post_comment(req: CommentRequest, user: dict = Depends(require_public_profile)) -> Any:
    """Post a comment on any news article. Requires public profile."""
    if not req.article_id or not req.content.strip():
        raise ValidationError("article_id and content required")

    comment = {
        "id": _make_comment_id(req.article_id, req.content),
        "article_id": req.article_id,
        "author": user.get("display_name") or user.get("email", "anon")[:50],
        "content": req.content[:2000],
        "created_at": datetime.now(UTC).isoformat(),
        "parent_id": req.parent_id,
        "likes": 0,
    }

    if req.article_id not in _COMMENTS:
        _COMMENTS[req.article_id] = []
    _COMMENTS[req.article_id].append(comment)

    return CommentResponse(**comment)


@router.get("/comments/{article_id}", response_model=list[CommentResponse])
async def get_comments(article_id: str) -> list:
    """Get comments for a specific article."""
    comments = _COMMENTS.get(article_id, [])
    return [CommentResponse(**c) for c in sorted(comments, key=lambda x: x["created_at"])]


# ─── Internal: for cron jobs to post scanner findings as news ─────


@router.post("/internal/scanner-alert")
async def post_scanner_alert(
    token_name: str, chain: str, risk_score: float, address: str, flags: str = ""
) -> dict[str, str]:
    """Internal endpoint for cron jobs to inject scanner findings into news feed."""
    global _NEWS_CACHE
    content_hash = hashlib.sha256(f"internal:{address}:{chain}".encode()).hexdigest()
    article = {
        "id": f"rmi-{content_hash[:12]}",
        "title": f"RMI Scanner: {token_name} ({chain.upper()}) - Risk {risk_score}/100",
        "url": f"https://rugmunch.io/scanner?address={address}&chain={chain}",
        "description": f"Scanner detected {token_name} on {chain}. Risk: {risk_score}/100. Flags: {flags}. Address: {address}",
        "source": "RMI Scanner",
        "published_at": datetime.now(UTC).isoformat(),
        "category": "security",
        "sentiment": "bearish" if risk_score > 50 else "neutral",
        "kind": "internal",
        "tier": "rmi",
        "risk_score": risk_score,
        "token_address": address,
        "chain": chain,
    }
    # Prepend to cache
    if _NEWS_CACHE:
        _NEWS_CACHE["articles"] = [article, *_NEWS_CACHE.get("articles", [])]
        _NEWS_CACHE["total"] = len(_NEWS_CACHE["articles"])
    return {"status": "injected", "id": article["id"]}
