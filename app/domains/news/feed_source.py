"""DB-backed ranked feed lane for the website news surfaces.

This is the canonical fail-open source for ``/api/v1/news/feed``,
``/headlines``, ``/categories`` and ``/stats``: one indexed read from
``news_items`` (the catalog table) followed by the deterministic ranking in
``app.domains.news.ranking``. No upstream fetch, no LLM, no per-row embed.

Rows are rejected when they cannot be rendered as journalism:
  * blank titles (aggregator junk such as bare emoji headlines)
  * future-dated ``published_at`` (mis-parsed publisher clocks)
  * raw Telegram wire noise (``tg:`` ids / ``t.me`` URLs)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.logging import get_logger
from app.domains.news.ranking import rank_items

log = get_logger(__name__)

_FEED_COLUMNS = (
    "n.news_id, n.url, n.title, n.summary, n.source, n.published_at, n.image_url, "
    "n.category, n.sentiment_score, n.sentiment_label, n.source_tier, n.source_trust, "
    "n.chains_mentioned, n.tokens_mentioned, n.bias, n.factual_rating, "
    "n.rating_confidence"
)

_FEED_WHERE = (
    "WHERE n.published_at > $1 AND n.published_at <= now() "
    "AND btrim(COALESCE(n.title, '')) <> '' "
    "AND NOT (n.news_id LIKE 'tg:%' OR n.url LIKE '%t.me/%')"
)


async def load_ranked_rows(
    *,
    window_hours: int = 72,
    pool_size: int = 600,
) -> list[dict[str, Any]]:
    """Read the recent window and return explained, deduped, ranked rows."""
    from app.catalog.service import get_catalog

    catalog = get_catalog()
    await catalog._init_stores()
    if not catalog._health.postgres:
        return []
    cutoff = datetime.now(UTC) - timedelta(hours=max(1, int(window_hours)))
    # Fragments are module constants; every value is bound ($1 cutoff, $2 limit).
    sql = "SELECT " + _FEED_COLUMNS + " FROM news_items n " + _FEED_WHERE  # noqa: S608
    sql += " ORDER BY n.published_at DESC LIMIT $2"
    try:
        async with catalog._pg_pool.acquire() as conn:
            rows = await conn.fetch(sql, cutoff, max(1, int(pool_size)))
    except Exception as exc:  # fail-open: the caller degrades to other lanes
        log.warning("feed_source_read_fail: %s: %s", type(exc).__name__, str(exc)[:200])
        return []
    return rank_items([dict(r) for r in rows], datetime.now(UTC))


def _as_article(row: dict[str, Any]) -> dict[str, Any]:
    """Map a ranked row onto the ``NewsArticle`` feed shape."""
    published = row.get("published_at")
    return {
        "id": row.get("news_id"),
        "title": row.get("title") or "",
        "url": row.get("url") or "",
        "description": row.get("summary") or "",
        "summary": row.get("summary") or "",
        "source": row.get("source") or "unknown",
        "published_at": published.isoformat() if isinstance(published, datetime) else None,
        "image_url": row.get("image_url"),
        "category": row.get("category"),
        "sentiment_score": row.get("sentiment_score"),
        "sentiment_label": row.get("sentiment_label"),
        "bias": row.get("bias"),
        "factual_rating": row.get("factual_rating"),
        "source_tier": row.get("source_tier"),
        "rating_confidence": row.get("rating_confidence"),
        "tokens_mentioned": list(row.get("tokens_mentioned") or []),
        "chains_mentioned": list(row.get("chains_mentioned") or []),
        "source_count": row.get("source_count") or 1,
        "also_covered_by": row.get("also_covered_by") or [],
        "score": row.get("score"),
        "score_components": row.get("score_components") or {},
        "kind": "news",
        "tier": "news",
    }


async def load_feed_articles(
    *,
    limit: int = 50,
    window_hours: int = 72,
    category: str | None = None,
) -> list[dict[str, Any]]:
    """Ranked, deduped, feed-ready articles — newest stories first by score."""
    rows = await load_ranked_rows(window_hours=window_hours)
    if category:
        want = category.strip().lower()
        rows = [r for r in rows if str(r.get("category") or "").lower() == want]
    return [_as_article(r) for r in rows[: max(1, int(limit))]]


def _feed_result(articles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Wrap ranked articles in the aggregate-feed envelope (None when empty)."""
    if not articles:
        return None
    return {
        "status": "success",
        "articles": articles,
        "total": len(articles),
        "sources": sorted({str(a.get("source") or "") for a in articles}),
        "source_count": len({a.get("source") or "" for a in articles}),
        "sentiment_summary": {},
        "tiers": ["news"],
    }


def is_ranked_result(result: dict[str, Any] | None) -> bool:
    """True when every article in the envelope carries a rank score.

    ``/headlines`` writes the unranked Redis aggregate into the same in-process
    memo that ``/feed`` reads, so a memo hit is not proof of a ranked envelope.
    """
    articles = (result or {}).get("articles") or []
    return bool(articles) and all(a.get("score") is not None for a in articles)


async def load_ranked_result(
    *,
    limit: int = 200,
    window_hours: int = 72,
    category: str | None = None,
) -> dict[str, Any] | None:
    """Feed-envelope wrapper around ``load_feed_articles`` (None when empty)."""
    articles = await load_feed_articles(limit=limit, window_hours=window_hours, category=category)
    return _feed_result(articles)
