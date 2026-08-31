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
    cluster_id: str | None = None  # T03 story cluster (persistent, content-derived)
    source_count: int = 0  # distinct sources covering this story
    corroborating_sources: int = 0  # how many sources report this
    # RAG-generated commentary (rag-feed only): 1-2 sentences, cryptorugmuncher
    # voice, cached by news_id so a story is never re-commented.
    commentary: str | None = None


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

# ─── Helpers ──────────────────────────────────────────────────────


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


# ─── Qdrant-backed rated articles ─────────────────────────────────
# Helpers live in app/routers/news_helpers.py (extracted to keep the
# router focused on request handling).
from app.routers.news_helpers import (  # noqa: E402 — module docstring precedes import block
    _count_corroborating,
    _normalize_sentiment_label,
    _point_to_article as _point_to_article,
    _qdrant_match_filter,
    _rate_article_dict,
    _scroll_news_v2 as _scroll_news_v2,
    _sort_articles_by,
)

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
    include_market: bool = Query(False, description="Include synthetic market-data items (CoinGecko/CryptoPanic)"),
    refresh: bool = Query(False, description="Force refresh, bypass cache"),
) -> Any:
    """Full aggregated news feed from all sources.

    Fast path: reads the Redis-backed aggregate that the background
    refresher maintains every ~5 min — instant, zero upstream calls.
    Cold-cache fallback: inline legacy fetch time-boxed to 12s.
    Empty results are never memoized (a failed fetch must not poison
    the cache for the TTL window).
    """
    global _NEWS_CACHE, _CACHE_TS

    result: dict[str, Any] | None = None

    if not refresh:
        # ── 1) In-process memo (3 min TTL) ──
        if _NEWS_CACHE and _CACHE_TS and (datetime.now(UTC) - _CACHE_TS) < _CACHE_TTL:
            result = _NEWS_CACHE
        else:
            # ── 2) Redis aggregate — instant read of the background refresh ──
            try:
                from app.databus.news_provider import get_aggregated_news

                agg = await get_aggregated_news(limit=200)
                agg_articles = (agg or {}).get("articles") or []
                if agg_articles:
                    result = {
                        "status": "success",
                        "articles": agg_articles,
                        "total": len(agg_articles),
                        "sources": sorted({str(a.get("source", "")) for a in agg_articles}),
                        "source_count": len({a.get("source", "") for a in agg_articles}),
                        "sentiment_summary": {},
                        "tiers": sorted({str(a.get("tier") or "news") for a in agg_articles}),
                    }
                    _NEWS_CACHE = result
                    _CACHE_TS = datetime.now(UTC)
            except Exception as e:
                logger.warning(f"Redis aggregate read failed: {e}")

    if result is None:
        # ── 3) Cold cache / forced refresh — inline legacy fetch, time-boxed ──
        try:
            from app.domains.news.news_service import get_news_service

            svc = get_news_service()
            fetched = await asyncio.wait_for(
                svc.fetch_all(
                    limit=200,
                    include_rss=include_rss,
                    include_reddit=include_reddit,
                    include_internal=include_internal,
                ),
                timeout=12.0,
            )
            if fetched.get("articles"):
                result = fetched
                _NEWS_CACHE = fetched
                _CACHE_TS = datetime.now(UTC)
            elif _NEWS_CACHE:
                result = _NEWS_CACHE
        except TimeoutError:
            logger.warning("News fetch_all timed out after 12s")
            if _NEWS_CACHE:
                result = _NEWS_CACHE
        except Exception as e:
            logger.error(f"News fetch failed: {e}")
            if _NEWS_CACHE:
                result = _NEWS_CACHE
            else:
                raise AppError(f"News aggregation failed: {e!s}") from e

    if result is None:
        # Nothing anywhere — explicit empty (never cached).
        result = {
            "status": "success",
            "articles": [],
            "total": 0,
            "sources": [],
            "source_count": 0,
            "sentiment_summary": {},
            "tiers": [],
            "cached": False,
            "fetched_at": datetime.now(UTC).isoformat(),
        }

    articles = result.get("articles", [])

    # News feed carries journalism only — synthetic market-data items
    # (CoinGecko trending/price "articles", kind="api") belong to the
    # markets widgets, not the news feed. Opt back in with include_market.
    if not include_market:
        articles = [
            a for a in articles if not (a.get("kind") == "api" and a.get("source", "") in {"CoinGecko", "CryptoPanic"})
        ]

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


# ─── RAG-Served Feed (rugbrain news_v2 + rerank) ──────────────────
# P2 (RAGBOT_NEXT_WINS): the consumer news surface is served FROM the RAG
# (rugbrain news_v2, reranked via POST /api/v1/rag/news-rerank) instead of
# the direct-API aggregator chain. Own-source priority: items from our own
# scans/alerts/KOL ingest (tg:/lookonchain:/scanner:/kol: news_ids, or
# RMI/Scanner/KOL/Lookonchain sources) rank above aggregator news.
# Fail-open: when rugbrain is unreachable the feed degrades to recency
# ranking of the same news_v2 points — honest data, never fabricated.

#: news_id prefixes written by OUR ingest lanes (Telegram KOL/scanner
#: channels, lookonchain watcher) — these are own-source by construction.
_OWN_NEWS_ID_PREFIXES = ("tg:", "lookonchain:", "scanner:", "kol:")
#: Substring markers in the source name for own-origin content.
_OWN_SOURCE_MARKERS = ("rmi", "scanner", "pryscraper", "kol", "lookonchain", "rugmunch", "callanalyser")

#: Default consumer query when the caller supplies none — broad enough to
#: surface security + market + protocol news from the news_v2 corpus.
_RAG_FEED_DEFAULT_QUERY = "latest crypto news security threats exploits market moves"


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


# ─── RAG commentary (rag-feed) ────────────────────────────────────
# After reranking, the top rag-feed items get a 1-2 sentence commentary
# line in the cryptorugmuncher voice (mirrors rugmunchbot channels
# style_guides "main" contract: name the token/mechanism/size, no AI slop,
# no hedging). Generated via the existing free-tier LLM chain
# (app.llm_config.chat_with_fallback — rugbrain exposes no commentary
# endpoint) and cached by news_id so a story is commented exactly once.
_COMMENTARY_CACHE: dict[str, str] = {}
_COMMENTARY_MAX_ITEMS = 10  # per rag-feed request — bounded LLM spend
_COMMENTARY_MAX_CHARS = 320

_COMMENTARY_SYSTEM = (
    "You write one-line commentary for a crypto news feed in the "
    "cryptorugmuncher voice: sharp analyst who has seen every cycle. "
    "1-2 sentences, max 40 words. Name the token, the mechanism, or the "
    "size when the article gives one. No hedging, no 'it's worth noting', "
    "no 'the crypto world is buzzing', no 'stay tuned', no NFA/DYOR. "
    "Plain text only — no markdown, no emoji, no links. Output ONLY the "
    "commentary line."
)


def _commentary_cache_key(article: dict[str, Any]) -> str:
    return str(article.get("news_id") or article.get("id") or article.get("url") or "")


async def _generate_commentary(article: dict[str, Any]) -> str:
    """1-2 sentence commentary for one article. Cached by news_id; "" on
    any failure (fail-open — the feed ships without commentary)."""
    key = _commentary_cache_key(article)
    if not key:
        return ""
    cached = _COMMENTARY_CACHE.get(key)
    if cached is not None:
        return cached
    title = str(article.get("title") or "").strip()
    summary = str(article.get("summary") or article.get("description") or "").strip()[:400]
    if not title:
        _COMMENTARY_CACHE[key] = ""
        return ""
    user = f"Headline: {title}\nSummary: {summary or '(none)'}"
    text = ""
    try:
        from app.llm_config import chat_with_fallback

        text = await chat_with_fallback(
            [
                {"role": "system", "content": _COMMENTARY_SYSTEM},
                {"role": "user", "content": user},
            ],
            "snappy",
            max_tokens=90,
        )
    except Exception as e:  # fail-open — commentary is additive
        logger.debug(f"commentary generation failed: {e}")
    text = " ".join(text.split())[:_COMMENTARY_MAX_CHARS].strip()
    # Reject degenerate outputs (echoed prompt scaffolding, empty).
    if text.lower().startswith(("headline:", "summary:")):
        text = ""
    _COMMENTARY_CACHE[key] = text
    return text


async def _attach_commentary(items: list[dict[str, Any]]) -> None:
    """Attach cached/generated commentary to the top rag-feed items.

    Bounded to the first ``_COMMENTARY_MAX_ITEMS`` (the LLM cost stays
    flat regardless of feed size); runs concurrently; never raises.
    """
    targets = items[:_COMMENTARY_MAX_ITEMS]
    results = await asyncio.gather(*(_generate_commentary(a) for a in targets), return_exceptions=True)
    for a, res in zip(targets, results, strict=True):
        a["commentary"] = res if isinstance(res, str) and res else None


# ─── Breaking-news latency observability ─────────────────────────
# Ingest→available latency: time from the source's published_at to the
# item appearing in /api/v1/news/rag-feed. Target < 5 min (300s).
# Observability ONLY — violations are logged, never blocked.
_BREAKING_NEWS_LATENCY_TARGET_S = 300.0


def _log_ingest_latency(items: list[dict[str, Any]]) -> None:
    """Log ingest→available latency per rag-feed item; warn on >5min.

    Uses the item's ``published_at`` (source publish time) vs now. Items
    without a parseable timestamp are skipped. Pure logging — never raises.
    """
    now = datetime.now(UTC)
    latencies: list[float] = []
    for a in items:
        raw = str(a.get("published_at") or "").strip()
        if not raw:
            continue
        try:
            published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=UTC)
            latencies.append((now - published).total_seconds())
        except ValueError:
            continue
    if not latencies:
        return
    violations = [lat for lat in latencies if lat > _BREAKING_NEWS_LATENCY_TARGET_S]
    if violations:
        worst = max(violations)
        logger.warning(
            "news_latency:target_exceeded target_s=%.0f violations=%d/%d worst_s=%.0f",
            _BREAKING_NEWS_LATENCY_TARGET_S,
            len(violations),
            len(latencies),
            worst,
        )
    logger.info(
        "news_latency:rag_feed_serve n=%d median_s=%.0f p90_s=%.0f max_s=%.0f",
        len(latencies),
        sorted(latencies)[len(latencies) // 2],
        sorted(latencies)[int(len(latencies) * 0.9)],
        max(latencies),
    )


class RagFeedResponse(NewsFeedResponse):
    """RAG-served feed — adds rerank/degradation provenance fields."""

    rag_served: bool = True
    reranked: bool = False
    own_source_count: int = 0


@router.get("/rag-feed", response_model=RagFeedResponse)
async def get_news_rag_feed(
    limit: int = Query(30, ge=1, le=100),
    category: str | None = Query(None, description="Optional news_v2 category filter"),
    query: str | None = Query(None, description="Optional rerank query (defaults to broad consumer query)"),
) -> Any:
    """RAG-served news feed: rugbrain news_v2, reranked, own-source first.

    Primary lane: rugbrain ``POST /api/v1/rag/news-rerank`` (embed + hybrid
    search + ~1s top-k rerank). Fallback lane (rugbrain down): the most
    recent news_v2 points ranked by recency — same corpus, honest order,
    ``reranked=false``. Never fabricates items; an empty corpus returns an
    explicit empty feed.
    """
    effective_query = (query or "").strip() or _RAG_FEED_DEFAULT_QUERY

    # ── 1) Reranked lane via rugbrain ────────────────────────────────
    items: list[dict[str, Any]] | None = None
    reranked = False
    try:
        from app.integrations.rugbrain_client import rugbrain_news_rerank

        items = await rugbrain_news_rerank(effective_query, limit=limit, category=category)
        reranked = bool(items)
    except Exception as e:
        logger.warning(f"rag-feed rerank lane failed: {e}")
        items = None

    # ── 2) Fallback lane: recency-ranked news_v2 scroll ─────────────
    if not items:
        points = await _scroll_news_v2(limit=150)
        articles = [_point_to_article(p) for p in points]
        # Dedupe by news_id (chunked points share one news_id).
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for a in articles:
            key = str(a.get("news_id") or a.get("id") or a.get("url") or "")
            if key and key in seen:
                continue
            seen.add(key)
            deduped.append(a)
        deduped.sort(key=lambda a: str(a.get("published_at") or ""), reverse=True)
        if category:
            deduped = [a for a in deduped if str(a.get("category") or "").lower() == category.lower()]
        items = deduped[:limit]

    # ── 3) Own-source priority (stable within-group order) ──────────
    items = _own_source_first(items)[:limit]
    own_count = sum(1 for a in items if _is_own_source(a))

    # ── 4) Rating enrichment + comment counts (same as /feed) ───────
    try:
        from app.domains.news.ratings import enrich_articles

        items = enrich_articles(items)
    except Exception as e:
        logger.warning(f"rag-feed rating enrichment failed: {e}")
    for a in items:
        aid = a.get("id", "")
        a["comment_count"] = len(_COMMENTS.get(aid, []))

    # ── 5) RAG commentary (top items, cached by news_id) ────────────
    try:
        await _attach_commentary(items)
    except Exception as e:
        logger.warning(f"rag-feed commentary attach failed: {e}")

    # ── 6) Breaking-news latency observability (log-only) ───────────
    try:
        _log_ingest_latency(items)
    except Exception as e:  # observability must never break the feed
        logger.debug(f"rag-feed latency logging failed: {e}")

    return RagFeedResponse(
        status="success",
        articles=[NewsArticle(**a) for a in items],
        total=len(items),
        sources=sorted({str(a.get("source") or "") for a in items if a.get("source")}),
        source_count=len({str(a.get("source") or "") for a in items if a.get("source")}),
        tiers=sorted({str(a.get("tier") or "news") for a in items}),
        cached=False,
        fetched_at=datetime.now(UTC).isoformat(),
        rag_served=True,
        reranked=reranked,
        own_source_count=own_count,
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
) -> dict[str, Any]:
    """Top headlines only - fast, lightweight.

    Reads from Redis cache first (populated by background task every 5 min),
    falls back to in-memory cache, then triggers background refresh.
    """
    global _NEWS_CACHE, _CACHE_TS

    # Check Redis cache first (cross-worker, always populated by lifespan)
    redis_articles = None
    try:
        from app.databus.news_provider import NEWS_CACHE_KEY, _redis_get

        redis_data = await _redis_get(NEWS_CACHE_KEY)
        if redis_data and redis_data.get("headlines"):
            hls = redis_data["headlines"]
            redis_articles = hls.get("articles", []) if isinstance(hls, dict) else []
    except Exception:  # noqa: S110 — best-effort cache
        pass  # Redis unavailable, fall through

    # Use Redis data if available (preferred over in-memory)
    if redis_articles:
        # Synchronize in-memory cache too
        _NEWS_CACHE = {
            "status": "success",
            "articles": redis_articles,
            "total": len(redis_articles),
            "sources": sorted({a.get("source", "") for a in redis_articles}),
            "source_count": len({a.get("source", "") for a in redis_articles}),
            "sentiment_summary": {},
            "tiers": sorted({a.get("tier", "news") for a in redis_articles}),
        }
        _CACHE_TS = datetime.now(UTC)

        articles = redis_articles
        if category:
            articles = [a for a in articles if a.get("category", "").lower() == category.lower()]
        articles = articles[:count]

        return {
            "headlines": [
                {
                    "title": a.get("title", ""),
                    "url": a.get("url", ""),
                    "source": a.get("source", ""),
                    "published_at": a.get("published_at", ""),
                    "category": a.get("category", ""),
                    "sentiment": a.get("sentiment", ""),
                    "kind": a.get("kind", ""),
                }
                for a in articles
            ],
            "count": len(articles),
            "cached": True,
            "source": "redis",
        }

    # Fallback to in-memory cache (legacy path)
    # Never trigger a full fetch from this endpoint — it's meant to be fast.
    if _NEWS_CACHE:
        result = _NEWS_CACHE
        # Trigger background refresh if stale
        if _CACHE_TS and (datetime.now(UTC) - _CACHE_TS) >= _CACHE_TTL:
            _ = asyncio.create_task(_refresh_cache(True, False, True))
    else:
        # First call — cache is empty. Trigger background seed and return empty.
        _ = asyncio.create_task(_refresh_cache(True, False, True))
        return {"headlines": [], "count": 0, "cached": False}

    articles = result.get("articles", [])
    if category:
        articles = [a for a in articles if a.get("category", "").lower() == category.lower()]
    articles = articles[:count]

    return {
        "headlines": [
            {
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "source": a.get("source", ""),
                "published_at": a.get("published_at", ""),
                "category": a.get("category", ""),
                "sentiment": a.get("sentiment", ""),
                "kind": a.get("kind", ""),
            }
            for a in articles
        ],
        "count": len(articles),
        "cached": bool(_CACHE_TS),
    }


# ─── Categories ───────────────────────────────────────────────────


@router.get("/categories")
async def get_categories() -> dict[str, list[dict[str, Any]]]:
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
async def get_stats() -> dict[str, Any]:
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
async def post_comment(req: CommentRequest, user: dict[str, Any] = Depends(require_public_profile)) -> Any:
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
async def get_comments(article_id: str) -> list[Any]:
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
    article: dict[str, Any] = {
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
