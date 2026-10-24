"""Grounded RAG citations via RugBrain — never invents data."""

from __future__ import annotations

import asyncio
from typing import Any

from app.api.v1.assistant.models import Citation
from app.core.logging import get_logger
from app.integrations.rugbrain_client import rugbrain_search

logger = get_logger(__name__)

_TIMEOUT = 5.0
_SNIPPET = 300


def _hit_to_citation(hit: dict[str, Any]) -> Citation:
    meta = hit.get("metadata") if isinstance(hit.get("metadata"), dict) else {}
    return Citation(
        collection=str(hit.get("collection") or meta.get("collection") or ""),
        doc_id=str(hit.get("id") or hit.get("doc_id") or meta.get("doc_id") or ""),
        score=float(hit.get("score") or 0.0),
        snippet=str(hit.get("content") or hit.get("text") or "")[:_SNIPPET],
    )


async def citations_for(query: str, limit: int = 3) -> list[Citation]:
    """Return RugBrain citations; empty list on any failure (no fabrication)."""
    if not query.strip():
        return []
    try:
        hits = await asyncio.wait_for(rugbrain_search(query, limit=limit), timeout=_TIMEOUT)
    except Exception as e:
        logger.warning("page_context_rag_failed: %s", e)
        return []
    return [_hit_to_citation(h) for h in (hits or []) if isinstance(h, dict)]
