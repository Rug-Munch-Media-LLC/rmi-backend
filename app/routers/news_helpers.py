"""Shared Qdrant-backed helpers for the news router.

Extracted from ``app/routers/news.py`` to keep the router focused on
request handling. These helpers are pure (no module-global state): they
only touch the Qdrant ``news_v2`` collection and the rating engine.

The rated-article API is backed by the RAG ``news_v2`` collection
(seeded by rmi-rag/scripts/news_to_rag.py). Each point payload:
  payload.content                    → article text
  payload.metadata.{title,source,url,published_at,category,
                    tokens_mentioned,chains_mentioned,news_id}
Ratings are computed on the fly via app/rag/ratings.py so old
articles (ingested before the rating engine existed) get a card too.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

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
