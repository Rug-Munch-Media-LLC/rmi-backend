"""Article browsing endpoints for the T28 news domain.

Split out of ``app.domains.news/router.py`` (2026-08-14). Mounted under
``app.domains.news.router`` — do not mount directly.

Routes:
  POST /api/v1/news                     list, paginated, filterable
  GET  /api/v1/news/trending            time-decay weighted
  GET  /api/v1/news/stories/{cluster_id}/sources  bias-spectrum panel
  GET  /api/v1/articles/{news_id}       single item
  POST /api/v1/articles/{news_id}/analyze  LLM analysis (LiteLLM)
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.catalog.llm_router import LLMRouter
from app.catalog.models import utcnow
from app.catalog.service import get_catalog
from app.domains.news.routes.schemas import (
    NewsAnalysisResponse,
    NewsItemOut,
    NewsListResponse,
    StorySourcesResponse,
    _adapt_legacy_row,
    trend_score,
)

router = APIRouter(tags=["news"])


# ── POST /api/v1/news (list with filters) ─────────────────────────
@router.post("/news", response_model=NewsListResponse)
async def list_news(
    chain: str | None = None,
    token: str | None = None,
    category: str | None = None,
    since_hours: int = Query(24, ge=1, le=720),
    limit: int = Query(20, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("recency", pattern="^(recency|relevance|sentiment)$"),
    clustered: bool = Query(False, description="T03: dedupe via MinHash+DBSCAN into stories"),
) -> NewsListResponse:
    """List news items with filters. Reads from both news_items (new) and crypto_news (legacy)."""
    catalog = get_catalog()
    await catalog._init_stores()
    items: list[NewsItemOut] = []

    # New table
    if catalog._health.postgres:
        try:
            # Multi-day stories need a wide fetch window when clustering;
            # the returned items are still filtered by since_hours below.
            fetch_hours = max(since_hours, 72) if clustered else since_hours
            cutoff = utcnow() - timedelta(hours=fetch_hours)
            query = (
                "SELECT n.news_id, n.url, n.title, n.summary, n.source, n.published_at, "
                "n.image_url, "
                "n.sentiment_score, n.chains_mentioned, n.tokens_mentioned, n.bias, "
                "n.sentiment_label, n.factual_rating, n.source_tier, n.rating_confidence, "
                "COALESCE(r.like_count, 0)::int AS upvotes, "
                "COALESCE(r.dislike_count, 0)::int AS downvotes, "
                "COALESCE(c.comment_count, 0)::int AS comment_count "
                "FROM news_items n "
                "LEFT JOIN (SELECT news_id, "
                "COUNT(*) FILTER (WHERE reaction = 'like')::int AS like_count, "
                "COUNT(*) FILTER (WHERE reaction = 'dislike')::int AS dislike_count "
                "FROM news_reactions GROUP BY news_id) r ON r.news_id = n.news_id "
                "LEFT JOIN (SELECT news_id, COUNT(*)::int AS comment_count "
                "FROM news_comments WHERE deleted_at IS NULL GROUP BY news_id) c "
                "ON c.news_id = n.news_id "
                "WHERE n.published_at > $1"
            )
            params: list[Any] = [cutoff]
            if chain:
                query += f" AND ${len(params) + 1} = ANY(n.chains_mentioned)"
                params.append(chain)
            if token:
                query += f" AND ${len(params) + 1} = ANY(n.tokens_mentioned)"
                params.append(token)
            if sort == "sentiment":
                query += " ORDER BY n.sentiment_score ASC NULLS LAST"
            else:
                query += " ORDER BY n.published_at DESC"
            # For clustering we need a wide pool of candidate items so
            # multi-day stories can dedupe; pagination limit still applies
            # to the raw (non-clustered) list.
            fetch_limit = max(limit, 500) if clustered else limit
            query += f" LIMIT {fetch_limit} OFFSET {offset}"
            async with catalog._pg_pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            for r in rows:
                items.append(
                    NewsItemOut(
                        news_id=r["news_id"],
                        url=r["url"],
                        title=r["title"],
                        summary=r["summary"] or "",
                        image_url=r.get("image_url"),
                        source=r["source"],
                        published_at=r["published_at"],
                        chains_mentioned=list(r["chains_mentioned"] or []),
                        tokens_mentioned=list(r["tokens_mentioned"] or []),
                        sentiment_score=r["sentiment_score"],
                        bias=r.get("bias"),
                        sentiment_label=r.get("sentiment_label"),
                        factual_rating=r.get("factual_rating"),
                        source_tier=r.get("source_tier"),
                        rating_confidence=r.get("rating_confidence"),
                        upvotes=r["upvotes"],
                        downvotes=r["downvotes"],
                        comment_count=r["comment_count"],
                    )
                )
        except (KeyError, ValueError, TypeError) as e:
            from app.core.logging import get_logger

            get_logger(__name__).warning(f"news_list_new_fail: {e}")

    # Legacy fallback (crypto_news)
    if not items and catalog._health.postgres:
        try:
            query = "SELECT id, title, content, url, source, sentiment, tickers, published, ingested_at FROM crypto_news WHERE 1=1"
            params = []
            if category:
                query += f" AND category = ${len(params) + 1}"
                params.append(category)
            query += " ORDER BY ingested_at DESC LIMIT $%d OFFSET $%d" % (
                len(params) + 1,
                len(params) + 2,
            )
            params.extend([limit, offset])
            async with catalog._pg_pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            for r in rows:
                items.append(_adapt_legacy_row(dict(r)))
        except (KeyError, ValueError, TypeError) as e:
            from app.core.logging import get_logger

            get_logger(__name__).warning(f"news_list_legacy_fail: {e}")

    # T03: cluster into stories if requested
    if clustered and items:
        from app.domains.news.clusterer import NewsItem, cluster_items, persist_clusters

        # Multi-day stories need a wide window; cluster over the full
        # requested range (defaults to at least 72h so day-spanning
        # stories still dedupe into one cluster).
        cluster_window = max(since_hours, 72) * 60  # minutes
        cluster_items_list = [
            NewsItem(
                id=it.news_id or "",
                title=it.title or "",
                body=it.summary or "",
                source=it.source or "",
                url=it.url or "",
                published_at=it.published_at or utcnow(),
                sentiment=it.sentiment_score or 0.0,
            )
            for it in items
        ]
        stories = cluster_items(cluster_items_list, window_minutes=cluster_window)
        # persist in background (don't block response)
        try:
            import asyncio

            asyncio.create_task(persist_clusters(stories))
        except (ConnectionError, OSError, TimeoutError):
            pass
        # Return clusters as synthetic items (representative title, first source)
        clustered_items = []
        for s in stories:
            clustered_items.append(
                NewsItemOut(
                    news_id=s.cluster_id,
                    url=s.source_urls[0] if s.source_urls else "",
                    title=f"[x{s.item_count}] {s.representative_title}",
                    summary=f"Story across {s.source_count} sources. "
                    f"Sentiment: {s.sentiment_avg:.2f}. "
                    f"Item IDs: {','.join(s.item_ids[:5])}",
                    source=", ".join(s.sources[:3]),
                    published_at=s.last_updated,
                    chains_mentioned=[],
                    tokens_mentioned=[],
                    sentiment_score=s.sentiment_avg,
                    source_count=s.source_count,
                    bias_distribution=s.bias_distribution,
                )
            )
        return NewsListResponse(items=clustered_items, total=len(clustered_items), offset=offset)

    return NewsListResponse(items=items, total=len(items), offset=offset)


# ── GET /api/v1/news/trending ─────────────────────────────────────
@router.get("/news/trending", response_model=NewsListResponse)
async def trending_news(
    window_hours: int = Query(168, ge=1, le=720),
    limit: int = Query(20, ge=1, le=100),
) -> NewsListResponse:
    """Time-decay trending. Reads from news_items (new) primary, falls back to crypto_news (legacy)."""
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        return NewsListResponse(items=[], total=0, offset=0)
    now = utcnow()
    cutoff = now - timedelta(hours=window_hours)
    items: list[NewsItemOut] = []
    # Primary: news_items
    try:
        async with catalog._pg_pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT n.news_id, n.url, n.title, n.summary, n.source, n.published_at, "
                "n.image_url, "
                "n.sentiment_score, n.chains_mentioned, n.tokens_mentioned, n.bias, "
                "n.sentiment_label, n.factual_rating, n.source_tier, n.rating_confidence, "
                "COALESCE(r.like_count, 0)::int AS upvotes, "
                "COALESCE(r.dislike_count, 0)::int AS downvotes, "
                "COALESCE(c.comment_count, 0)::int AS comment_count "
                "FROM news_items n "
                "LEFT JOIN (SELECT news_id, "
                "COUNT(*) FILTER (WHERE reaction = 'like')::int AS like_count, "
                "COUNT(*) FILTER (WHERE reaction = 'dislike')::int AS dislike_count "
                "FROM news_reactions GROUP BY news_id) r ON r.news_id = n.news_id "
                "LEFT JOIN (SELECT news_id, COUNT(*)::int AS comment_count "
                "FROM news_comments WHERE deleted_at IS NULL GROUP BY news_id) c "
                "ON c.news_id = n.news_id "
                "WHERE n.published_at > $1 "
                "ORDER BY n.published_at DESC LIMIT 500",
                cutoff,
            )
        for r in rows:
            hours_old = max(0, (now - r["published_at"]).total_seconds() / 3600)
            item = NewsItemOut(
                news_id=r["news_id"],
                url=r["url"] or "",
                title=r["title"] or "",
                summary=r["summary"] or "",
                image_url=r.get("image_url"),
                source=r["source"] or "unknown",
                published_at=r["published_at"],
                chains_mentioned=list(r["chains_mentioned"] or []),
                tokens_mentioned=list(r["tokens_mentioned"] or []),
                sentiment_score=r["sentiment_score"],
                bias=r.get("bias"),
                sentiment_label=r.get("sentiment_label"),
                factual_rating=r.get("factual_rating"),
                source_tier=r.get("source_tier"),
                rating_confidence=r.get("rating_confidence"),
                upvotes=r["upvotes"],
                downvotes=r["downvotes"],
                comment_count=r["comment_count"],
            )
            item.score = round(trend_score(hours_old, item.source or "unknown", item.sentiment_score), 4)
            items.append(item)
    except (KeyError, ValueError, TypeError) as e:
        from app.core.logging import get_logger

        get_logger(__name__).warning(f"trending_new_fail: {e}")

    # Fallback: crypto_news (legacy) if no new items
    if not items:
        try:
            cutoff_epoch = cutoff.timestamp()
            async with catalog._pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, title, content, url, source, sentiment, tickers, "
                    "published, ingested_at, category "
                    "FROM crypto_news "
                    "WHERE ingested_at > $1 "
                    "ORDER BY ingested_at DESC LIMIT 500",
                    cutoff_epoch,
                )
            for r in rows:
                d = dict(r)
                hours_old = 0.0
                try:
                    if d.get("ingested_at"):
                        hours_old = max(0, (now.timestamp() - float(d["ingested_at"])) / 3600)
                except (ConnectionError, OSError, TimeoutError):
                    pass
                item = _adapt_legacy_row(d)
                item.score = round(trend_score(hours_old, item.source or "unknown", item.sentiment_score), 4)
                items.append(item)
        except (ValueError, KeyError, TypeError) as e:
            from app.core.logging import get_logger

            get_logger(__name__).warning(f"trending_legacy_fail: {e}")

    items.sort(key=lambda x: x.score or 0, reverse=True)
    return NewsListResponse(items=items[:limit], total=len(items), offset=0)


# ── GET /api/v1/news/stories/{cluster_id}/sources ─────────────────
@router.get("/news/stories/{cluster_id}/sources", response_model=StorySourcesResponse)
async def get_story_sources(cluster_id: str) -> StorySourcesResponse:
    """Per-story source distribution for the bias-spectrum panel.

    Given a story ``cluster_id`` (persisted by the T03 clustering job),
    return the distinct sources, per-source URLs, bias distribution and
    sentiment average so the panel can render who covers a story and from
    which bias angles. 404 when the cluster is unknown.
    """
    from app.domains.news.clusterer import load_story_sources

    data = await load_story_sources(cluster_id)
    if not data:
        raise HTTPException(404, "story cluster not found")
    return StorySourcesResponse(**data)


# ── GET /api/v1/articles/{news_id} ────────────────────────────────
# Moved off /api/v1/news/{news_id} per Phase 2 Wave 2 (AUDIT-2026-Q3.md)
# so app.routers.news (feed/headlines/sources/...) can coexist.
@router.get("/articles/{news_id}", response_model=NewsItemOut)
async def get_news(news_id: str) -> NewsItemOut:
    """Single news item. Searches both news_items and crypto_news."""
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    try:
        async with catalog._pg_pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT n.news_id, n.url, n.title, n.summary, n.source, n.published_at, "
                "n.image_url, "
                "n.sentiment_score, n.chains_mentioned, n.tokens_mentioned, "
                "COALESCE(r.like_count, 0)::int AS upvotes, "
                "COALESCE(r.dislike_count, 0)::int AS downvotes, "
                "COALESCE(c.comment_count, 0)::int AS comment_count "
                "FROM news_items n "
                "LEFT JOIN (SELECT news_id, "
                "COUNT(*) FILTER (WHERE reaction = 'like')::int AS like_count, "
                "COUNT(*) FILTER (WHERE reaction = 'dislike')::int AS dislike_count "
                "FROM news_reactions GROUP BY news_id) r ON r.news_id = n.news_id "
                "LEFT JOIN (SELECT news_id, COUNT(*)::int AS comment_count "
                "FROM news_comments WHERE deleted_at IS NULL GROUP BY news_id) c "
                "ON c.news_id = n.news_id "
                "WHERE n.news_id=$1",
                news_id,
            )
            if r:
                return NewsItemOut(
                    news_id=r["news_id"],
                    url=r["url"],
                    title=r["title"],
                    summary=r["summary"] or "",
                    image_url=r.get("image_url"),
                    source=r["source"],
                    published_at=r["published_at"],
                    chains_mentioned=list(r["chains_mentioned"] or []),
                    tokens_mentioned=list(r["tokens_mentioned"] or []),
                    sentiment_score=r["sentiment_score"],
                    upvotes=r["upvotes"],
                    downvotes=r["downvotes"],
                    comment_count=r["comment_count"],
                )
            r2 = await conn.fetchrow(
                "SELECT id, title, content, url, source, sentiment, tickers, "
                "published, ingested_at, category "
                "FROM crypto_news WHERE id=$1",
                news_id,
            )
        if r2:
            return _adapt_legacy_row(dict(r2))
        raise HTTPException(404, "news item not found")
    except HTTPException:
        raise
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(500, f"news_get_fail: {e}") from e


# ── POST /api/v1/articles/{news_id}/analyze ───────────────────────
# Same move rationale as GET /api/v1/articles/{news_id} above.
@router.post("/articles/{news_id}/analyze", response_model=NewsAnalysisResponse)
async def analyze_news(news_id: str) -> NewsAnalysisResponse:
    """Generate LLM analysis via LiteLLM. Falls back to None if LLM unreachable."""
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    try:
        # Fetch the news item
        item: NewsItemOut | None = None
        async with catalog._pg_pool.acquire() as conn:
            r = await conn.fetchrow(
                "SELECT news_id, url, title, summary, source, published_at, "
                "image_url, "
                "sentiment_score, chains_mentioned, tokens_mentioned "
                "FROM news_items WHERE news_id=$1",
                news_id,
            )
            if r:
                item = NewsItemOut(
                    news_id=r["news_id"],
                    url=r["url"],
                    title=r["title"],
                    summary=r["summary"] or "",
                    image_url=r.get("image_url"),
                    source=r["source"],
                    published_at=r["published_at"],
                    chains_mentioned=list(r["chains_mentioned"] or []),
                    tokens_mentioned=list(r["tokens_mentioned"] or []),
                    sentiment_score=r["sentiment_score"],
                )
            if not item:
                r2 = await conn.fetchrow(
                    "SELECT id, title, content, url, source, sentiment, tickers, "
                    "published, ingested_at FROM crypto_news WHERE id=$1",
                    news_id,
                )
                if r2:
                    item = _adapt_legacy_row(dict(r2))
        if not item:
            raise HTTPException(404, "news item not found")
        # Build a NewsItem for the LLM router
        from app.catalog.models import NewsItem

        ni = NewsItem(
            news_id=item.news_id,
            url=item.url or "https://unknown.local",  # HttpUrl requires non-empty
            title=item.title or "",
            summary=item.summary or "",
            body_markdown=item.summary or "",
            source=item.source or "unknown",
            published_at=item.published_at or utcnow(),
            ingested_at=utcnow(),
        )
        llm = LLMRouter()
        analysis = await llm.analyze_news(ni)
        if analysis is None:
            return NewsAnalysisResponse(news_id=news_id, analysis=None, error="LLM router unavailable")
        return NewsAnalysisResponse(news_id=news_id, analysis=analysis, model="deepseek-v3")
    except HTTPException:
        raise
    except (KeyError, ValueError, TypeError) as e:
        return NewsAnalysisResponse(news_id=news_id, analysis=None, error=str(e))


__all__ = [
    "analyze_news",
    "get_news",
    "get_story_sources",
    "list_news",
    "router",
    "trending_news",
]
