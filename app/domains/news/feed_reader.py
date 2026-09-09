"""Canonical Qdrant ``news_v2`` read helpers (feed reader).

Moved down from ``app/routers/news_helpers.py`` (import-boundary debt — the
news domain and the chat tool-use layer both consume these helpers, and
``app.domains`` must not import from ``app.routers``). This module is the
single source of truth; ``app/routers/news_helpers.py`` re-exports the same
objects so every router-level importer keeps working unchanged.

These helpers are pure (no module-global state): they turn raw Qdrant
``news_v2`` points into article dicts.

The rated-article API is backed by the RAG ``news_v2`` collection
(seeded by rugbrain/scripts/news_to_rag.py). Each point payload:
  payload.content                    → article text
  payload.metadata.{title,source,url,published_at,category,
                    tokens_mentioned,chains_mentioned,news_id}

**Boundary (ARCHITECTURE.md §2):** rugbrain-owned collections are read
through ``app.integrations.rugbrain_client`` only — this module never opens
its own Qdrant connection. ``_scroll_news_v2`` delegates to
``rugbrain_news_scroll``; the client owns the direct-Qdrant fallback lane.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_NEWS_SCROLL_WINDOW = 100  # Qdrant scroll batch size


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
        "image_url": meta.get("image_url") or None,
        "summary": (content or "")[:500],
        "description": (content or "")[:500],
        "category": meta.get("category") or "",
        "tokens_mentioned": list(meta.get("tokens_mentioned") or []),
        "chains_mentioned": list(meta.get("chains_mentioned") or []),
        "news_id": meta.get("news_id") or "",
        "group_id": meta.get("group_id"),
        "cluster_id": meta.get("cluster_id"),
        "source_count": meta.get("source_count") or 0,
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

    Delegates to ``app.integrations.rugbrain_client.rugbrain_news_scroll`` —
    the thin-client lane for reading rugbrain-owned collections
    (ARCHITECTURE.md §2: rugbrain via HTTP API / thin client only). The
    client owns the Qdrant base URL, fail-open behavior and direct-Qdrant
    fallback. Returns raw point dicts (id + payload); callers sort by
    ``published_at`` afterwards. Never raises.
    """
    from app.integrations.rugbrain_client import rugbrain_news_scroll

    return await rugbrain_news_scroll(filters=filters, limit=limit)
