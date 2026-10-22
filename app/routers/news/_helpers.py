"""Shared models, cache state, and helpers for the news sub-routers.

Split out of the former monolithic ``app/routers/news.py`` so the
feed/search/admin sub-routers can share one source of truth. Mutable
module state (``_NEWS_CACHE``, ``_CACHE_TS``, ``_COMMENTS``) lives here;
sub-routers mutate it via this module's namespace.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core.logging import get_logger

logger = get_logger(__name__)

# Sub-routers mount under the parent router's /api/v1/news prefix.
router = APIRouter(tags=["news"])


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
    cluster_id: str | None = None  # T03 story cluster (persistent, content-derived)
    source_count: int = 0  # distinct sources covering this story
    corroborating_sources: int = 0  # how many sources report this
    # RAG-generated commentary (rag-feed only): 1-2 sentences, cryptorugmuncher
    # voice, cached by news_id so a story is never re-commented.
    commentary: str | None = None
    # Deterministic rank (app.domains.news.ranking) — explained, never opaque.
    score: float | None = None
    score_components: dict[str, float] = Field(default_factory=dict)
    also_covered_by: list[dict[str, Any]] = Field(default_factory=list)


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
_NEWS_CACHE: dict[str, Any] = {}
_CACHE_TS: datetime | None = None
_CACHE_TTL = timedelta(minutes=3)

# In-memory comments store (ephemeral - persists via Redis later)
_COMMENTS: dict[str, list[dict[str, Any]]] = {}

# ─── Qdrant-backed rated articles ─────────────────────────────────
# Helpers live in app/routers/news_helpers.py (extracted to keep the
# router focused on request handling).
from app.routers.news_helpers import (  # noqa: E402 — module docstring precedes import block
    _count_corroborating as _count_corroborating,
    _normalize_sentiment_label as _normalize_sentiment_label,
    _point_to_article as _point_to_article,
    _qdrant_match_filter as _qdrant_match_filter,
    _rate_article_dict as _rate_article_dict,
    _scroll_news_v2 as _scroll_news_v2,
    _sort_articles_by as _sort_articles_by,
)


def _make_comment_id(article_id: str, content: str) -> str:
    h = hashlib.sha256(f"{article_id}:{content}:{datetime.now(UTC).isoformat()}".encode()).hexdigest()
    return f"comment-{h[:12]}"


async def _refresh_cache(include_rss: bool, include_reddit: bool, include_internal: bool) -> None:
    """Background cache refresh task — delegates to Redis-backed news_provider."""
    global _NEWS_CACHE, _CACHE_TS
    try:
        from app.databus.news_provider import refresh_news_cache as redis_refresh

        # Primary: use the Redis-backed refresh (populates Redis + returns data)
        data = await redis_refresh()
        if data and data.get("headlines"):
            hls = data["headlines"]
            articles = hls.get("articles", []) if isinstance(hls, dict) else []
            if articles:
                # Convert to the format expected by the router
                _NEWS_CACHE = {
                    "status": "success",
                    "articles": articles,
                    "total": len(articles),
                    "sources": sorted({a.get("source", "") for a in articles}),
                    "source_count": len({a.get("source", "") for a in articles}),
                    "sentiment_summary": {},
                    "tiers": sorted({a.get("tier", "news") for a in articles}),
                }
                _CACHE_TS = datetime.now(UTC)
                logger.info(f"News cache refreshed via Redis: {len(articles)} articles")
                return

        # Fallback: try the old news_service path
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
            f"News cache refreshed (legacy): {result.get('total', 0)} articles from {result.get('source_count', 0)} sources"
        )
    except Exception as e:
        logger.warning(f"Background news refresh failed: {e}")


# ─── Own-source priority (rag-feed) ───────────────────────────────
# P2 (RAGBOT_NEXT_WINS): the consumer news surface is served FROM the RAG
# (rugbrain news_v2, reranked via POST /api/v1/rag/news-rerank) instead of
# the direct-API aggregator chain. Own-source priority: items from our own
# scans/alerts/KOL ingest (tg:/lookonchain:/scanner:/kol: news_ids, or
# RMI/Scanner/KOL/Lookonchain sources) rank above aggregator news.

#: news_id prefixes written by OUR ingest lanes (Telegram KOL/scanner
#: channels, lookonchain watcher) — these are own-source by construction.
_OWN_NEWS_ID_PREFIXES = ("tg:", "lookonchain:", "scanner:", "kol:")
#: Substring markers in the source name for own-origin content.
_OWN_SOURCE_MARKERS = ("rmi", "scanner", "pryscraper", "kol", "lookonchain", "rugmunch", "callanalyser")


def _is_own_source(article: dict[str, Any]) -> bool:
    """True when the article originates from our own ingest lanes."""
    news_id = str(article.get("news_id") or article.get("id") or "")
    if news_id.startswith(_OWN_NEWS_ID_PREFIXES):
        return True
    source = str(article.get("source") or "").lower()
    return any(marker in source for marker in _OWN_SOURCE_MARKERS)


def _own_source_first(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stable partition: own-source items above aggregator news.

    Order WITHIN each group is preserved (rerank or recency order), so this
    is a priority boost, not a re-ranking.
    """
    own = [a for a in articles if _is_own_source(a)]
    rest = [a for a in articles if not _is_own_source(a)]
    return own + rest


# ─── Bot Push: POST /api/v1/news/articles + /ingest ──────────────
# Lets the rugmunchbot push articles into the Qdrant news_v2 corpus.
# Each article is deduped by URL hash, rated, and stored via the
# existing rugbrain ingest lane (embed + store). Fail-open on Qdrant.


class BotArticle(BaseModel):
    """A single article pushed by the bot."""

    title: str = ""
    url: str = ""
    source: str = ""
    description: str | None = None
    published_at: str | None = None
    image_url: str | None = None
    tokens_mentioned: list[str] = Field(default_factory=list)


class BotArticlesRequest(BaseModel):
    """Request body for POST /api/v1/news/articles."""

    articles: list[BotArticle] = Field(default_factory=list)


class BotIngestRequest(BaseModel):
    """Request body for POST /api/v1/news/ingest (raw text + URL)."""

    title: str = ""
    url: str = ""
    source: str = ""
    content: str = ""
    published_at: str | None = None
    image_url: str | None = None
    tokens_mentioned: list[str] = Field(default_factory=list)


def _url_hash(url: str) -> str:
    """Stable URL hash for dedup (normalised URL → sha256 prefix)."""
    norm = url.strip().rstrip("/").lower()
    return hashlib.sha256(norm.encode()).hexdigest()[:24]


async def _qdrant_available() -> bool:
    """Lightweight Qdrant reachability probe (fail-closed on error)."""
    try:
        import httpx

        from app.integrations.rugbrain_client import _qdrant_base

        base = _qdrant_base()
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{base}/collections")
        return r.status_code == 200
    except Exception as e:
        logger.warning("news_qdrant_probe_failed err=%s", e)
        return False


async def _news_url_exists(url_hash: str) -> bool:
    """True if a news_v2 point already carries this URL hash (dedup)."""
    filters = [_qdrant_match_filter("metadata.url_hash", url_hash)]
    points = await _scroll_news_v2(filters=filters, limit=5)
    return bool(points)


async def _store_bot_article(article: BotArticle) -> bool:
    """Rate + store a single article into Qdrant news_v2 (fail-open)."""
    from app.integrations.rugbrain_client import rugbrain_ingest

    content = article.description or article.title
    rating = _rate_article_dict(
        {
            "title": article.title,
            "description": article.description or "",
            "source": article.source,
        }
    )
    metadata = {
        "title": article.title,
        "url": article.url,
        "url_hash": _url_hash(article.url),
        "source": article.source,
        "published_at": article.published_at or "",
        "image_url": article.image_url or "",
        "tokens_mentioned": article.tokens_mentioned or [],
        "category": "news",
        "news_id": f"bot:{_url_hash(article.url)}",
        "factuality": rating.get("factuality", "reported"),
        "source_reputation": rating.get("source_reputation", "unknown"),
        "market_impact": rating.get("market_impact", "low"),
        "sentiment": rating.get("sentiment", "NEUTRAL"),
        "sentiment_score": rating.get("sentiment_score", 0.0),
        "rating_confidence": rating.get("confidence", 0.3),
        "ingested_at": datetime.now(UTC).isoformat(),
    }
    ok = await rugbrain_ingest(
        content=content,
        collection="news_v2",
        doc_id=f"news:{_url_hash(article.url)}",
        metadata=metadata,
        source=article.source or "bot",
    )
    return bool(ok)
