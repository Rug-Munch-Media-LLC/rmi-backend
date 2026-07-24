"""T28 News Intelligence Product.

Per v4.0 §T28. Four endpoints surface the news pipeline as a product:
  POST /api/v1/news              list, paginated, filterable
  GET  /api/v1/news/trending     time-decay weighted
  GET  /api/v1/news/{news_id}    single item
  POST /api/v1/news/{news_id}/analyze  LLM analysis (LiteLLM)

Data sources:
  - crypto_news (legacy table, 1750+ items from RSS feeds)
  - news_items (new catalog table, populated by RSS ingest)
  - Qdrant embeddings for semantic search (rag_embedding_id)

Time-decay scoring for /trending:
  score = recency_decay * source_authority * social_velocity * abs(sentiment)
  recency_decay = 0.5 ** (hours_old / 6)        half-life 6h
  source_authority: tier-1 (CoinDesk, The Block) = 1.0, tier-2 = 0.5, tier-3 = 0.2
  social_velocity: tweets/shares in last hour (1 + n/100, capped at 2x)
  abs(sentiment): polarizing news ranks higher
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from app.catalog.llm_router import LLMRouter
from app.catalog.models import utcnow
from app.catalog.service import get_catalog

# Prefix is /api/v1 (not /api/v1/news) per Phase 2 Wave 2 (AUDIT-2026-Q3.md):
# the catch-all endpoints moved to /api/v1/articles/{news_id} so that
# app.routers.news can mount /api/v1/news/{feed,headlines,sources,...}
# without being shadowed by a {news_id} catch-all.
router = APIRouter(prefix="/api/v1", tags=["news"])


# ── Time-decay scoring (per v4.0 §T28) ────────────────────────────
SOURCE_AUTHORITY: dict[str, float] = {
    # Tier 1 - major crypto-native outlets
    "coindesk": 1.0,
    "the block": 1.0,
    "decrypt": 1.0,
    "cointelegraph": 1.0,
    # Tier 2 - solid crypto coverage
    "beincrypto": 0.7,
    "u.today": 0.7,
    "crypto.news": 0.7,
    "blockworks": 0.8,
    # Tier 3 - general / RSS aggregators
    "google-crypto": 0.5,
    "reddit-crypto": 0.4,
    "twitter-crypto": 0.3,
}


def recency_decay(hours_old: float, half_life: float = 6.0) -> float:
    """Exponential decay: 1.0 at 0h, 0.5 at half_life, 0.25 at 2*half_life."""
    if hours_old < 0:
        return 1.0
    return 0.5 ** (hours_old / half_life)


def source_authority(source: str) -> float:
    s = (source or "").lower().strip()
    for key, val in SOURCE_AUTHORITY.items():
        if key in s:
            return val
    return 0.3  # unknown source


def trend_score(
    hours_old: float, source: str, sentiment: float | None, social_velocity: int = 0
) -> float:
    """Composite trending score per v4.0 formula."""
    s_auth = source_authority(source)
    s_vel = min(2.0, 1.0 + social_velocity / 100.0)
    s_sent = 1.0 + abs(sentiment or 0.0)
    return recency_decay(hours_old) * s_auth * s_vel * s_sent


# ── Response models ────────────────────────────────────────────────
class NewsItemOut(BaseModel):
    model_config = ConfigDict(strict=False)  # accept None for any field with default

    news_id: str | None = None
    url: str | None = None
    title: str | None = None
    summary: str | None = ""
    source: str | None = "unknown"
    published_at: datetime | None = None
    chains_mentioned: list[str] = Field(default_factory=list)
    tokens_mentioned: list[str] = Field(default_factory=list)
    sentiment_score: float | None = None
    score: float | None = None  # only set for /trending


class NewsListResponse(BaseModel):
    items: list[NewsItemOut]
    total: int
    offset: int


class NewsAnalysisResponse(BaseModel):
    news_id: str
    analysis: str | None
    model: str | None = None
    error: str | None = None


# ── Row adapter (legacy crypto_news → NewsItemOut) ───────────────
def _adapt_legacy_row(row: dict) -> NewsItemOut:
    """Convert a crypto_news row to NewsItemOut shape."""
    # published is a text field in legacy; try ISO parse
    pub_str = row.get("published") or row.get("ingested_at")
    pub_dt = utcnow()
    if pub_str:
        try:
            if isinstance(pub_str, (int, float)):
                pub_dt = datetime.fromtimestamp(float(pub_str), tz=UTC)
            else:
                # Try common formats
                for fmt in (
                    "%Y-%m-%dT%H:%M:%S.%fZ",
                    "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S",
                ):
                    try:
                        pub_dt = datetime.strptime(str(pub_str)[:19], fmt).replace(tzinfo=UTC)
                        break
                    except ValueError:
                        continue
        except (ConnectionError, OSError, TimeoutError):
            pass
    return NewsItemOut(
        news_id=row.get("id", ""),
        url=row.get("url", ""),
        title=row.get("title", ""),
        summary=(row.get("content") or "")[:500],
        source=row.get("source", "unknown"),
        published_at=pub_dt,
        chains_mentioned=[],
        tokens_mentioned=row.get("tickers") or [],
        sentiment_score=row.get("sentiment"),
    )


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
            cutoff = utcnow() - timedelta(hours=since_hours)
            query = "SELECT news_id, url, title, summary, source, published_at, sentiment_score, chains_mentioned, tokens_mentioned FROM news_items WHERE published_at > $1"
            params: list[Any] = [cutoff]
            if chain:
                query += f" AND ${len(params) + 1} = ANY(chains_mentioned)"
                params.append(chain)
            if token:
                query += f" AND ${len(params) + 1} = ANY(tokens_mentioned)"
                params.append(token)
            if sort == "sentiment":
                query += " ORDER BY sentiment_score ASC NULLS LAST"
            else:
                query += " ORDER BY published_at DESC"
            query += f" LIMIT {limit} OFFSET {offset}"
            async with catalog._pg_pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            for r in rows:
                items.append(
                    NewsItemOut(
                        news_id=r["news_id"],
                        url=r["url"],
                        title=r["title"],
                        summary=r["summary"] or "",
                        source=r["source"],
                        published_at=r["published_at"],
                        chains_mentioned=list(r["chains_mentioned"] or []),
                        tokens_mentioned=list(r["tokens_mentioned"] or []),
                        sentiment_score=r["sentiment_score"],
                    )
                )
        except Exception as e:
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
        except Exception as e:
            from app.core.logging import get_logger

            get_logger(__name__).warning(f"news_list_legacy_fail: {e}")

    # T03: cluster into stories if requested
    if clustered and items:
        from app.domains.news.clusterer import NewsItem, cluster_items, persist_clusters

        cluster_items_list = [
            NewsItem(
                id=it.news_id,
                title=it.title,
                body=it.summary or "",
                source=it.source or "",
                url=it.url or "",
                published_at=it.published_at,
                sentiment=it.sentiment_score or 0.0,
            )
            for it in items
        ]
        stories = cluster_items(cluster_items_list)
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
                    summary=f"Story across {len(s.sources)} sources. "
                    f"Sentiment: {s.sentiment_avg:.2f}. "
                    f"Item IDs: {','.join(s.item_ids[:5])}",
                    source=", ".join(s.sources[:3]),
                    published_at=s.last_updated,
                    chains_mentioned=[],
                    tokens_mentioned=[],
                    sentiment_score=s.sentiment_avg,
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
                "SELECT news_id, url, title, summary, source, published_at, "
                "sentiment_score, chains_mentioned, tokens_mentioned "
                "FROM news_items "
                "WHERE published_at > $1 "
                "ORDER BY published_at DESC LIMIT 500",
                cutoff,
            )
        for r in rows:
            hours_old = max(0, (now - r["published_at"]).total_seconds() / 3600)
            item = NewsItemOut(
                news_id=r["news_id"],
                url=r["url"] or "",
                title=r["title"] or "",
                summary=r["summary"] or "",
                source=r["source"] or "unknown",
                published_at=r["published_at"],
                chains_mentioned=list(r["chains_mentioned"] or []),
                tokens_mentioned=list(r["tokens_mentioned"] or []),
                sentiment_score=r["sentiment_score"],
            )
            item.score = round(trend_score(hours_old, item.source, item.sentiment_score), 4)
            items.append(item)
    except Exception as e:
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
                item.score = round(trend_score(hours_old, item.source, item.sentiment_score), 4)
                items.append(item)
        except Exception as e:
            from app.core.logging import get_logger

            get_logger(__name__).warning(f"trending_legacy_fail: {e}")

    items.sort(key=lambda x: x.score or 0, reverse=True)
    return NewsListResponse(items=items[:limit], total=len(items), offset=0)


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
                "SELECT news_id, url, title, summary, source, published_at, "
                "sentiment_score, chains_mentioned, tokens_mentioned "
                "FROM news_items WHERE news_id=$1",
                news_id,
            )
            if r:
                return NewsItemOut(
                    news_id=r["news_id"],
                    url=r["url"],
                    title=r["title"],
                    summary=r["summary"] or "",
                    source=r["source"],
                    published_at=r["published_at"],
                    chains_mentioned=list(r["chains_mentioned"] or []),
                    tokens_mentioned=list(r["tokens_mentioned"] or []),
                    sentiment_score=r["sentiment_score"],
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
    except Exception as e:
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
            return NewsAnalysisResponse(
                news_id=news_id, analysis=None, error="LLM router unavailable"
            )
        return NewsAnalysisResponse(news_id=news_id, analysis=analysis, model="deepseek-v3")
    except HTTPException:
        raise
    except Exception as e:
        return NewsAnalysisResponse(news_id=news_id, analysis=None, error=str(e))
