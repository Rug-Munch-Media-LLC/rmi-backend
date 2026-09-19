"""Shared helpers for the news router (ratings + corroboration).

Extracted from ``app/routers/news.py`` to keep the router focused on
request handling. The raw ``news_v2`` read primitives
(``_point_to_article`` / ``_qdrant_match_filter`` / ``_scroll_news_v2``)
now live DOWN in ``app.domains.news.feed_reader`` — they are consumed by
the news domain and the chat tool-use layer too, and ``app.domains`` must
not import from ``app.routers`` (see ``.importlinter``). This module
re-exports those same objects so every router-level importer keeps working
unchanged, and keeps the Ground News-style rating-card helpers.

Ratings are computed on the fly via app/rag/ratings.py so old articles
(ingested before the rating engine existed) get a card too.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.news.feed_reader import (  # re-export — canonical impl below app.domains
    _point_to_article as _point_to_article,
    _qdrant_match_filter as _qdrant_match_filter,
    _scroll_news_v2 as _scroll_news_v2,
)

logger = get_logger(__name__)


def _rate_article_dict(article: dict[str, Any]) -> dict[str, Any]:
    """Compute the Ground News rating card for an article dict.

    Uses app/rag/ratings.py rate_article(). Never raises — malformed
    articles get a conservative default card.
    """
    try:
        from app.integrations.rag_helpers.ratings import rate_article

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

    Uses the Qdrant ``cluster_id`` / ``group_id`` when present; otherwise
    groups by a normalised title hash so same-story variants from different
    sources corroborate each other.
    """
    group = article.get("cluster_id") or article.get("group_id") or ""
    if not group and article.get("title"):
        norm = "".join(ch for ch in article["title"].lower() if ch.isalnum())[:80]
        group = f"title:{norm}"
    if not group:
        return 0
    sources: set[str] = set()
    for a in articles:
        a_group = a.get("cluster_id") or a.get("group_id") or ""
        if not a_group and a.get("title"):
            a_norm = "".join(ch for ch in a["title"].lower() if ch.isalnum())[:80]
            a_group = f"title:{a_norm}"
        if a_group and a_group == group and a.get("source"):
            sources.add(str(a["source"]))
    # exclude the article itself (same source) from the corroboration count
    return max(len(sources) - 1, 0)
