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

import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.catalog.llm_router import LLMRouter
from app.catalog.models import utcnow
from app.catalog.service import get_catalog
from app.core.rate_limiter import RateLimitDependency

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


def trend_score(hours_old: float, source: str, sentiment: float | None, social_velocity: int = 0) -> float:
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
    bias: str | None = None
    sentiment_label: str | None = None
    factual_rating: str | None = None
    source_tier: int | None = None
    rating_confidence: float | None = None
    source_count: int | None = None
    bias_distribution: dict[str, int] = Field(default_factory=dict)
    score: float | None = None  # only set for /trending
    upvotes: int = 0  # from news_reactions (NEWS-PAGE-DESIGN.md §3)
    downvotes: int = 0  # from news_reactions
    comment_count: int = 0  # visible comments on news_comments


class NewsListResponse(BaseModel):
    items: list[NewsItemOut]
    total: int
    offset: int


class NewsAnalysisResponse(BaseModel):
    news_id: str
    analysis: str | None
    model: str | None = None
    error: str | None = None


class StorySourcesResponse(BaseModel):
    """Per-story source distribution for the bias-spectrum panel."""

    cluster_id: str
    representative_title: str = ""
    first_seen: datetime | None = None
    last_updated: datetime | None = None
    item_count: int = 0
    sentiment_avg: float = 0.0
    source_count: int = 0
    sources: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    bias_distribution: dict[str, int] = Field(default_factory=dict)
    item_ids: list[str] = Field(default_factory=list)


# ── Row adapter (legacy crypto_news → NewsItemOut) ───────────────
def _adapt_legacy_row(row: dict[str, Any]) -> NewsItemOut:
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
            # Multi-day stories need a wide fetch window when clustering;
            # the returned items are still filtered by since_hours below.
            fetch_hours = max(since_hours, 72) if clustered else since_hours
            cutoff = utcnow() - timedelta(hours=fetch_hours)
            query = (
                "SELECT n.news_id, n.url, n.title, n.summary, n.source, n.published_at, "
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
            return NewsAnalysisResponse(news_id=news_id, analysis=None, error="LLM router unavailable")
        return NewsAnalysisResponse(news_id=news_id, analysis=analysis, model="deepseek-v3")
    except HTTPException:
        raise
    except (KeyError, ValueError, TypeError) as e:
        return NewsAnalysisResponse(news_id=news_id, analysis=None, error=str(e))


# ── Comments + reactions (NEWS-PAGE-DESIGN.md §3) ──────────────────
# Tables: news_reactions, news_comments, news_comment_reactions (see
# app/catalog/init_schema.py + alembic c1d2e3f4a5b6).
#
# Auth binding: the news domain has no auth of its own. Writes bind to the
# unified session identity — the per-user JWT in the ``rmi_token`` cookie or
# ``Authorization: Bearer`` validated by app.auth.jwt_validation (the same
# Tier-2 identity the auth middleware attaches to request.state.user). Reads
# are public; anonymous callers get guest=True and my_* fields stay None.
# The optional ``wallet`` claim is the wallet fallback identity.

_COMMENT_BODY_MAX = 2000
_HTML_TAG_RE = re.compile(r"<[^>]*>")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_comment_body(body: str) -> str:
    """Strip HTML + control chars, collapse whitespace, cap at 2000 chars."""
    text = _HTML_TAG_RE.sub("", body)
    text = _CONTROL_CHAR_RE.sub("", text)
    text = " ".join(text.split())
    return text[:_COMMENT_BODY_MAX]


def _coerce_reaction(value: str | None) -> Literal["like", "dislike"] | None:
    if value in ("like", "dislike"):
        return cast("Literal['like', 'dislike']", value)
    return None


async def _session_user(request: Request) -> dict[str, Any] | None:
    """Unified session user claims (JWT cookie / Bearer, Tier-2 identity)."""
    from app.auth.jwt_validation import get_current_user

    return await get_current_user(request)


async def _resolve_identity(request: Request) -> tuple[str | None, bool]:
    """Return (user_id, guest). Falls back to the wallet claim, else anon."""
    user = await _session_user(request)
    if not user:
        return None, True
    user_id = str(user.get("id") or user.get("sub") or "").strip() or None
    if user_id:
        return user_id, False
    wallet = str(user.get("wallet") or "").strip() or None
    if wallet:
        return wallet, False
    return None, True


class ReactionRequest(BaseModel):
    reaction: Literal["like", "dislike"]


class ReactionsSummary(BaseModel):
    news_id: str
    like_count: int = 0
    dislike_count: int = 0
    my_reaction: Literal["like", "dislike"] | None = None


class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=_COMMENT_BODY_MAX)
    parent_id: str | None = None


class CommentOut(BaseModel):
    id: str
    news_id: str
    user_id: str
    parent_id: str | None = None
    body: str | None = None
    created_at: datetime
    edited_at: datetime | None = None
    deleted: bool = False
    like_count: int = 0
    dislike_count: int = 0
    my_reaction: Literal["like", "dislike"] | None = None
    is_mine: bool = False


class CommentListResponse(BaseModel):
    items: list[CommentOut]
    total: int


class CommentReactionResponse(BaseModel):
    comment_id: str
    like_count: int = 0
    dislike_count: int = 0
    my_reaction: Literal["like", "dislike"] | None = None


async def _reaction_counts(catalog: Any, news_id: str) -> tuple[int, int]:
    """(like_count, dislike_count) for a news item."""
    async with catalog._pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT COUNT(*) FILTER (WHERE reaction = 'like')::int AS like_count, "
            "COUNT(*) FILTER (WHERE reaction = 'dislike')::int AS dislike_count "
            "FROM news_reactions WHERE news_id = $1",
            news_id,
        )
    return ((row["like_count"] if row else 0), (row["dislike_count"] if row else 0))


# ── POST /api/v1/news/{news_id}/react ─────────────────────────────
@router.post("/news/{news_id}/react", response_model=ReactionsSummary)
async def react_to_news(news_id: str, req: ReactionRequest, request: Request) -> ReactionsSummary:
    """Upsert a like/dislike vote. Sending the same reaction again toggles it off."""
    user_id, guest = await _resolve_identity(request)
    if guest or not user_id:
        raise HTTPException(401, "authentication required to react")
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    toggled_off = False
    async with catalog._pg_pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT reaction FROM news_reactions WHERE news_id=$1 AND user_id=$2",
            news_id,
            user_id,
        )
        if existing and existing["reaction"] == req.reaction:
            toggled_off = True
            await conn.execute(
                "DELETE FROM news_reactions WHERE news_id=$1 AND user_id=$2",
                news_id,
                user_id,
            )
        else:
            await conn.execute(
                "INSERT INTO news_reactions (news_id, user_id, reaction, created_at) "
                "VALUES ($1, $2, $3, NOW()) "
                "ON CONFLICT (news_id, user_id) DO UPDATE SET reaction = EXCLUDED.reaction, created_at = NOW()",
                news_id,
                user_id,
                req.reaction,
            )
    like_count, dislike_count = await _reaction_counts(catalog, news_id)
    return ReactionsSummary(
        news_id=news_id,
        like_count=like_count,
        dislike_count=dislike_count,
        my_reaction=None if toggled_off else req.reaction,
    )


# ── GET /api/v1/news/{news_id}/reactions ──────────────────────────
@router.get("/news/{news_id}/reactions", response_model=ReactionsSummary)
async def get_news_reactions(news_id: str, request: Request) -> ReactionsSummary:
    """Reaction counts + the caller's vote (null when anonymous or no vote)."""
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    like_count, dislike_count = await _reaction_counts(catalog, news_id)
    user_id, _ = await _resolve_identity(request)
    my_reaction: Literal["like", "dislike"] | None = None
    if user_id:
        async with catalog._pg_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT reaction FROM news_reactions WHERE news_id=$1 AND user_id=$2",
                news_id,
                user_id,
            )
        my_reaction = _coerce_reaction(row["reaction"] if row else None)
    return ReactionsSummary(
        news_id=news_id,
        like_count=like_count,
        dislike_count=dislike_count,
        my_reaction=my_reaction,
    )


# ── GET /api/v1/news/{news_id}/comments ───────────────────────────
@router.get("/news/{news_id}/comments", response_model=CommentListResponse)
async def list_comments(news_id: str, request: Request) -> CommentListResponse:
    """List comments for an article. Flat list; threads assemble client-side
    via parent_id. Deleted comments return as tombstones (body=None, deleted=True)."""
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    user_id, _ = await _resolve_identity(request)
    async with catalog._pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT c.id, c.news_id, c.user_id, c.parent_id, c.body, c.created_at, "
            "c.edited_at, c.deleted_at, "
            "COALESCE(r.like_count, 0)::int AS like_count, "
            "COALESCE(r.dislike_count, 0)::int AS dislike_count "
            "FROM news_comments c "
            "LEFT JOIN (SELECT comment_id, "
            "COUNT(*) FILTER (WHERE reaction = 'like')::int AS like_count, "
            "COUNT(*) FILTER (WHERE reaction = 'dislike')::int AS dislike_count "
            "FROM news_comment_reactions GROUP BY comment_id) r ON r.comment_id = c.id "
            "WHERE c.news_id = $1 "
            "ORDER BY c.created_at ASC, c.id ASC",
            news_id,
        )
        my_rows: dict[str, str] = {}
        if user_id and rows:
            my_rows = {
                str(row["comment_id"]): row["reaction"]
                for row in await conn.fetch(
                    "SELECT comment_id, reaction FROM news_comment_reactions "
                    "WHERE user_id = $1 AND comment_id = ANY($2::uuid[])",
                    user_id,
                    [r["id"] for r in rows],
                )
            }
    items = []
    for r in rows:
        is_deleted = r["deleted_at"] is not None
        items.append(
            CommentOut(
                id=str(r["id"]),
                news_id=r["news_id"],
                user_id=r["user_id"],
                parent_id=str(r["parent_id"]) if r["parent_id"] else None,
                body=None if is_deleted else r["body"],
                created_at=r["created_at"],
                edited_at=r["edited_at"],
                deleted=is_deleted,
                like_count=r["like_count"],
                dislike_count=r["dislike_count"],
                my_reaction=_coerce_reaction(my_rows.get(str(r["id"]))),
                is_mine=(user_id is not None and r["user_id"] == user_id),
            )
        )
    return CommentListResponse(items=items, total=len(items))


# ── POST /api/v1/news/{news_id}/comments ──────────────────────────
@router.post(
    "/news/{news_id}/comments",
    response_model=CommentOut,
    dependencies=[Depends(RateLimitDependency(limit_key="news_comment_create", rate=5.0 / 60.0, burst=5))],
)
async def create_comment(news_id: str, req: CommentCreate, request: Request) -> CommentOut:
    """Create a comment (optionally threaded via parent_id). Auth required."""
    user_id, guest = await _resolve_identity(request)
    if guest or not user_id:
        raise HTTPException(401, "authentication required to comment")
    body = _sanitize_comment_body(req.body)
    if not body:
        raise HTTPException(422, "comment body empty after sanitization")
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    parent_id: UUID | None = None
    async with catalog._pg_pool.acquire() as conn:
        # Article must exist (news_items or legacy crypto_news).
        exists = await conn.fetchrow("SELECT 1 FROM news_items WHERE news_id=$1", news_id)
        if not exists:
            exists = await conn.fetchrow("SELECT 1 FROM crypto_news WHERE id=$1", news_id)
        if not exists:
            raise HTTPException(404, "news item not found")
        if req.parent_id:
            try:
                parent_id = UUID(req.parent_id)
            except ValueError:
                raise HTTPException(422, "invalid parent_id") from None
            parent = await conn.fetchrow(
                "SELECT 1 FROM news_comments WHERE id=$1 AND news_id=$2",
                parent_id,
                news_id,
            )
            if not parent:
                raise HTTPException(404, "parent comment not found")
        row = await conn.fetchrow(
            "INSERT INTO news_comments (news_id, user_id, parent_id, body, created_at) "
            "VALUES ($1, $2, $3, $4, NOW()) "
            "RETURNING id, news_id, user_id, parent_id, body, created_at, edited_at, deleted_at",
            news_id,
            user_id,
            parent_id,
            body,
        )
    return CommentOut(
        id=str(row["id"]),
        news_id=row["news_id"],
        user_id=row["user_id"],
        parent_id=str(row["parent_id"]) if row["parent_id"] else None,
        body=row["body"],
        created_at=row["created_at"],
        edited_at=row["edited_at"],
        deleted=False,
        like_count=0,
        dislike_count=0,
        my_reaction=None,
        is_mine=True,
    )


# ── DELETE /api/v1/news/comment/{comment_id} ──────────────────────
@router.delete("/news/comment/{comment_id}")
async def delete_comment(comment_id: str, request: Request) -> dict[str, bool]:
    """Soft-delete a comment (tombstone). Author, admin, or moderator only."""
    user_id, guest = await _resolve_identity(request)
    if guest or not user_id:
        raise HTTPException(401, "authentication required")
    try:
        cid = UUID(comment_id)
    except ValueError:
        raise HTTPException(422, "invalid comment id") from None
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    async with catalog._pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, deleted_at FROM news_comments WHERE id=$1",
            cid,
        )
        if not row or row["deleted_at"] is not None:
            raise HTTPException(404, "comment not found")
        user = await _session_user(request)
        role = str((user or {}).get("role") or "USER").upper()
        if row["user_id"] != user_id and role not in ("ADMIN", "MODERATOR"):
            raise HTTPException(403, "not authorized to delete this comment")
        await conn.execute("UPDATE news_comments SET deleted_at = NOW() WHERE id=$1", cid)
    return {"ok": True}


# ── POST /api/v1/news/comment/{comment_id}/react ──────────────────
@router.post("/news/comment/{comment_id}/react", response_model=CommentReactionResponse)
async def react_to_comment(comment_id: str, req: ReactionRequest, request: Request) -> CommentReactionResponse:
    """Upsert a like/dislike on a comment; same reaction again toggles it off."""
    user_id, guest = await _resolve_identity(request)
    if guest or not user_id:
        raise HTTPException(401, "authentication required to react")
    try:
        cid = UUID(comment_id)
    except ValueError:
        raise HTTPException(422, "invalid comment id") from None
    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        raise HTTPException(503, "postgres unavailable")
    my_reaction: Literal["like", "dislike"] | None = None
    async with catalog._pg_pool.acquire() as conn:
        exists = await conn.fetchrow("SELECT 1 FROM news_comments WHERE id=$1", cid)
        if not exists:
            raise HTTPException(404, "comment not found")
        existing = await conn.fetchrow(
            "SELECT reaction FROM news_comment_reactions WHERE comment_id=$1 AND user_id=$2",
            cid,
            user_id,
        )
        if existing and existing["reaction"] == req.reaction:
            await conn.execute(
                "DELETE FROM news_comment_reactions WHERE comment_id=$1 AND user_id=$2",
                cid,
                user_id,
            )
        else:
            await conn.execute(
                "INSERT INTO news_comment_reactions (comment_id, user_id, reaction, created_at) "
                "VALUES ($1, $2, $3, NOW()) "
                "ON CONFLICT (comment_id, user_id) DO UPDATE SET reaction = EXCLUDED.reaction, created_at = NOW()",
                cid,
                user_id,
                req.reaction,
            )
            my_reaction = req.reaction
        counts = await conn.fetchrow(
            "SELECT COUNT(*) FILTER (WHERE reaction = 'like')::int AS like_count, "
            "COUNT(*) FILTER (WHERE reaction = 'dislike')::int AS dislike_count "
            "FROM news_comment_reactions WHERE comment_id = $1",
            cid,
        )
    return CommentReactionResponse(
        comment_id=str(cid),
        like_count=counts["like_count"] if counts else 0,
        dislike_count=counts["dislike_count"] if counts else 0,
        my_reaction=my_reaction,
    )
