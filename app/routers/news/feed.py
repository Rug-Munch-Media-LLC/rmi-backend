"""News feed sub-router: /feed, /rag-feed, /headlines, /categories, /stats.

The rag-feed lane is RAG-served (rugbrain news_v2 + rerank) with
own-source priority, image backfill, and cached LLM commentary.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from fastapi import Query

# Package-namespace indirection: tests patch attributes on
# ``app.routers.news`` (e.g. _scroll_news_v2, _generate_commentary,
# logger, _IMAGE_BACKFILL_CACHE); reading through the package module at
# call time keeps those patches effective.
import app.routers.news as _pkg
from app.core.errors import AppError
from app.domains.news.feed_source import is_ranked_result, load_ranked_result
from app.routers.news import _helpers as _h
from app.routers.news._helpers import (
    _COMMENTS,
    NewsArticle,
    NewsFeedResponse,
    _refresh_cache,
    logger,
    router as router,
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

    Fast path: the ranked DB lane (``feed_source`` — one indexed read of
    ``news_items`` + deterministic ranking), memoized for 3 min. The memo is
    only trusted when every row carries a ``score``; ``/headlines`` writes the
    unranked Redis aggregate into that same memo. Fallbacks: Redis aggregate,
    then an inline legacy fetch time-boxed to 12s. Empty results are never
    memoized (a failed fetch must not poison the cache for the TTL window).
    """
    result: dict[str, Any] | None = None

    if not refresh:
        # ── 1) In-process memo (3 min TTL) — ranked envelopes only ──
        # /headlines writes the unranked Redis aggregate into this same memo, so
        # a memo hit alone is not enough: an unscored envelope falls through.
        if (
            _h._NEWS_CACHE
            and _h._CACHE_TS
            and (datetime.now(UTC) - _h._CACHE_TS) < _h._CACHE_TTL
            and is_ranked_result(_h._NEWS_CACHE)
        ):
            result = _h._NEWS_CACHE
        # ── 2) Ranked DB lane — writes the scored envelope into the memo ──
        if result is None:
            try:
                result = await load_ranked_result(limit=200, category=category)
                if result is not None:
                    _h._NEWS_CACHE = result
                    _h._CACHE_TS = datetime.now(UTC)
            except Exception as e:
                logger.warning(f"Ranked feed lane failed: {e}")
        # ── 3) Redis aggregate — instant read of the background refresh ──
        if result is None:
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
                    _h._NEWS_CACHE = result
                    _h._CACHE_TS = datetime.now(UTC)
            except Exception as e:
                logger.warning(f"Redis aggregate read failed: {e}")

    if result is None:
        # ── 4) Cold cache / forced refresh — inline legacy fetch, time-boxed ──
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
                _h._NEWS_CACHE = fetched
                _h._CACHE_TS = datetime.now(UTC)
            elif _h._NEWS_CACHE:
                result = _h._NEWS_CACHE
        except TimeoutError:
            logger.warning("News fetch_all timed out after 12s")
            if _h._NEWS_CACHE:
                result = _h._NEWS_CACHE
        except Exception as e:
            logger.error(f"News fetch failed: {e}")
            if _h._NEWS_CACHE:
                result = _h._NEWS_CACHE
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
        cached=bool(_h._NEWS_CACHE),
        fetched_at=(_h._CACHE_TS or datetime.now(UTC)).isoformat(),
    )


# ─── RAG-Served Feed (rugbrain news_v2 + rerank) ──────────────────
# Fail-open: when rugbrain is unreachable the feed degrades to recency
# ranking of the same news_v2 points — honest data, never fabricated.

#: Default consumer query when the caller supplies none — broad enough to
#: surface security + market + protocol news from the news_v2 corpus.
_RAG_FEED_DEFAULT_QUERY = "latest crypto news security threats exploits market moves"


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
    results = await asyncio.gather(*(_pkg._generate_commentary(a) for a in targets), return_exceptions=True)
    for a, res in zip(targets, results, strict=True):
        a["commentary"] = res if isinstance(res, str) and res else None


# ─── Image doctrine (2026-08-31): every rag-feed item carries an image ──
# News on the website must ALWAYS have an image. Items missing image_url
# get an on-demand og:image fetch from the article URL (cached by URL so a
# story is fetched at most once per process); a story with no resolvable
# image lands on the branded RugMunch placeholder. Fail-open: fetch errors
# never break the feed.
_IMAGE_BACKFILL_CACHE: dict[str, str] = {}
_IMAGE_BACKFILL_MAX = 20  # per request — bounded network spend
_BRANDED_PLACEHOLDER_IMAGE = "https://rugmunch.io/og/rugmunch-intel.png"


async def _backfill_images(items: list[dict[str, Any]]) -> None:
    """Ensure every feed item has a usable ``image_url`` (in place).

    Order: carried image → cached og:image → fresh og:image fetch (bounded,
    concurrent) → branded placeholder. Never raises.
    """
    from app.domains.news.og_image import fetch_og_image

    missing: list[int] = []
    for i, a in enumerate(items):
        img = str(a.get("image_url") or "").strip()
        if img.startswith(("http://", "https://")):
            continue
        url = str(a.get("url") or "").strip()
        cached = _pkg._IMAGE_BACKFILL_CACHE.get(url, "")
        if cached:
            a["image_url"] = cached
            continue
        if url.startswith(("http://", "https://")):
            missing.append(i)
        else:
            a["image_url"] = _pkg._BRANDED_PLACEHOLDER_IMAGE
    if missing:
        bounded = missing[:_IMAGE_BACKFILL_MAX]
        fetched = await asyncio.gather(
            *(fetch_og_image(str(items[i].get("url") or "")) for i in bounded),
            return_exceptions=True,
        )
        for i, res in zip(bounded, fetched, strict=True):
            img = res if isinstance(res, str) and res.startswith(("http://", "https://")) else ""
            if img:
                _pkg._IMAGE_BACKFILL_CACHE[str(items[i].get("url") or "")] = img
            items[i]["image_url"] = img or _pkg._BRANDED_PLACEHOLDER_IMAGE
        # Anything beyond the bounded window gets the placeholder (never a
        # naked card); the cache warms on the next request.
        for i in missing[len(bounded) :]:
            items[i]["image_url"] = _pkg._BRANDED_PLACEHOLDER_IMAGE


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
        _pkg.logger.warning(
            "news_latency:target_exceeded target_s=%.0f violations=%d/%d worst_s=%.0f",
            _BREAKING_NEWS_LATENCY_TARGET_S,
            len(violations),
            len(latencies),
            worst,
        )
    _pkg.logger.info(
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
        points = await _pkg._scroll_news_v2(limit=150)
        articles = [_pkg._point_to_article(p) for p in points]
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
    items = _pkg._own_source_first(items)[:limit]
    own_count = sum(1 for a in items if _pkg._is_own_source(a))

    # ── 4) Rating enrichment + comment counts (same as /feed) ───────
    try:
        from app.domains.news.ratings import enrich_articles

        items = enrich_articles(items)
    except Exception as e:
        logger.warning(f"rag-feed rating enrichment failed: {e}")
    for a in items:
        aid = a.get("id", "")
        a["comment_count"] = len(_COMMENTS.get(aid, []))

    # ── 5) Image doctrine: every item carries an image (og:image backfill,
    #    cached; branded placeholder fallback) ─────────────────────────
    try:
        await _pkg._backfill_images(items)
    except Exception as e:
        logger.warning(f"rag-feed image backfill failed: {e}")

    # ── 6) RAG commentary (top items, cached by news_id) ────────────
    try:
        await _pkg._attach_commentary(items)
    except Exception as e:
        logger.warning(f"rag-feed commentary attach failed: {e}")

    # ── 6) Breaking-news latency observability (log-only) ───────────
    try:
        _pkg._log_ingest_latency(items)
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
        _h._NEWS_CACHE = {
            "status": "success",
            "articles": redis_articles,
            "total": len(redis_articles),
            "sources": sorted({a.get("source", "") for a in redis_articles}),
            "source_count": len({a.get("source", "") for a in redis_articles}),
            "sentiment_summary": {},
            "tiers": sorted({a.get("tier", "news") for a in redis_articles}),
        }
        _h._CACHE_TS = datetime.now(UTC)

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
    if _h._NEWS_CACHE:
        result = _h._NEWS_CACHE
        # Trigger background refresh if stale
        if _h._CACHE_TS and (datetime.now(UTC) - _h._CACHE_TS) >= _h._CACHE_TTL:
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
        "cached": bool(_h._CACHE_TS),
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
