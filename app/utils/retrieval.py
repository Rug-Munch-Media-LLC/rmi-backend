"""Lightweight retrieval utilities wrapping rugbrain HTTP API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.integrations.rugbrain_client import rugbrain_search

logger = logging.getLogger(__name__)


@dataclass
class ScoredHit:
    """Minimal scored hit compatible with HybridRetriever interface."""

    content: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    final_score: float = 0.0


async def hybrid_retrieve(
    query: str,
    collection: str,
    top_k: int = 10,
) -> list[ScoredHit]:
    """Search a RAG collection via rugbrain and return ScoredHit list.

    Acts as a drop-in replacement for ``HybridRetriever.retrieve()``
    when rerank=False is sufficient.
    """
    try:
        results = await rugbrain_search(
            query=query,
            collections=[collection],
            limit=top_k,
        )
        hits: list[ScoredHit] = []
        for r in results:
            score = float(r.get("rrf_score", r.get("score", 0.0)))
            hits.append(
                ScoredHit(
                    content=r.get("content", "") or r.get("text", ""),
                    score=score,
                    final_score=score,
                    metadata=r.get("metadata", {}) or {},
                )
            )
        return hits
    except Exception as e:
        logger.debug("rugbrain hybrid_retrieve failed: %s", e)
        return []
