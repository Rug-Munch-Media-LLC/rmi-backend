"""
News Router — THE Crypto News Aggregator Feed
=============================================
200+ sources. Real data only. No fake articles.

Endpoints:
  GET /api/v1/news/feed       — Full aggregated feed with filters
  GET /api/v1/news/sources    — Active sources list with counts
  GET /api/v1/news/sentiment  — Real-time sentiment overview
  GET /api/v1/news/headlines  — Top headlines only
  GET /api/v1/news/stats      — Source/category statistics
  POST /api/v1/news/comment   — Comment on article (social)
  GET /api/v1/news/comments/:article_id — Get comments for article
"""

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import require_public_profile

logger = logging.getLogger(__name__)

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

# In-memory comments store (ephemeral — persists via Redis later)
_COMMENTS: dict[str, list[dict]] = {}

# ─── Helpers ──────────────────────────────────────────────────────


def _make_comment_id(article_id: str, content: str) -> str:
    h = hashlib.md5(f"{article_id}:{content}:{datetime.now(UTC).isoformat()}".encode()).hexdigest()
    return f"comment-{h[:12]}"


async def _refresh_cache(include_rss: bool, include_reddit: bool, include_internal: bool):
    """Background cache refresh task."""
    global _NEWS_CACHE, _CACHE_TS
    try:
        from app.news_service import get_news_service

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


# ─── Main Feed ────────────────────────────────────────────────────


@router.get("/feed", response_model=NewsFeedResponse)
async def get_news_feed(
    limit: int = Query(50, ge=1, le=200),
    sentiment: str | None = Query(None, description="bullish, bearish, neutral, slightly_bullish, slightly_bearish"),
    source: str | None = Query(None),
    category: str | None = Query(None),
    tier: str | None = Query(None, description="news, social, market, rmi, api"),
    kind: str | None = Query(None, description="external, internal, social, api"),
    include_rss: bool = Query(True),
    include_reddit: bool = Query(True),
    include_internal: bool = Query(True),
    refresh: bool = Query(False, description="Force refresh, bypass cache"),
):
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
            # First load or forced refresh — fetch inline but with limits
            try:
                from app.news_service import get_news_service

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
                    raise HTTPException(status_code=500, detail=f"News aggregation failed: {e!s}")

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


# ─── Headlines ────────────────────────────────────────────────────


@router.get("/headlines")
async def get_headlines(
    count: int = Query(10, ge=1, le=50),
    category: str | None = Query(None),
):
    """Top headlines only — fast, lightweight."""
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


# ─── Sources ──────────────────────────────────────────────────────


@router.get("/sources")
async def get_news_sources():
    """Get all active news sources."""
    feed = await get_news_feed(limit=200, refresh=False)
    sources = {}
    for a in feed.articles:
        src = a.source
        sources[src] = sources.get(src, 0) + 1

    return {
        "sources": [
            {
                "name": name,
                "article_count": count,
                "tier": a.tier if hasattr(a, "tier") else "unknown",
            }
            for name, count in sorted(sources.items(), key=lambda x: -x[1])
            for a in [next((art for art in feed.articles if art.source == name), None)]
        ],
        "total_sources": len(sources),
    }


# ─── Sentiment ────────────────────────────────────────────────────


@router.get("/sentiment")
async def get_sentiment():
    """Real-time sentiment overview from current news feed."""
    feed = await get_news_feed(limit=200, refresh=False)
    return {
        "sentiment": feed.sentiment_summary,
        "sample_size": feed.total,
        "fetched_at": feed.fetched_at,
        "trending": [a.title for a in feed.articles[:5] if a.sentiment in ("bullish", "bearish")],
    }


# ─── Categories ───────────────────────────────────────────────────


@router.get("/categories")
async def get_categories():
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
async def get_stats():
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
async def post_comment(req: CommentRequest, user: dict = Depends(require_public_profile)):
    """Post a comment on any news article. Requires public profile."""
    if not req.article_id or not req.content.strip():
        raise HTTPException(status_code=400, detail="article_id and content required")

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
async def get_comments(article_id: str):
    """Get comments for a specific article."""
    comments = _COMMENTS.get(article_id, [])
    return [CommentResponse(**c) for c in sorted(comments, key=lambda x: x["created_at"])]


# ─── Internal: for cron jobs to post scanner findings as news ─────


@router.post("/internal/scanner-alert")
async def post_scanner_alert(token_name: str, chain: str, risk_score: float, address: str, flags: str = ""):
    """Internal endpoint for cron jobs to inject scanner findings into news feed."""
    global _NEWS_CACHE
    content_hash = hashlib.md5(f"internal:{address}:{chain}".encode()).hexdigest()
    article = {
        "id": f"rmi-{content_hash[:12]}",
        "title": f"RMI Scanner: {token_name} ({chain.upper()}) — Risk {risk_score}/100",
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
