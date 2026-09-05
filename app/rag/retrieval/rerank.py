"""Relevance re-scoring with text-feature signals."""

from __future__ import annotations

import re
from typing import Any

from app.rag.config import RAG_HYBRID_ALPHA
from app.rag.retrieval.helpers import ScoredHit


class RelevanceScorer:
    """Post-hoc relevance scoring with text-feature signals.

    Computes a composite score from:
      - Base similarity (from vector/keyword search)
      - Keyword overlap ratio between query and hit content
      - Position bonus (earlier results get a small boost)
    """

    def rescore(
        self,
        hits: list[dict[str, Any]],
        query: str,
        alpha: float = RAG_HYBRID_ALPHA,
    ) -> list[ScoredHit]:
        """Re-rank hits using additional relevance signals."""
        if not hits:
            return []

        query_terms = set(re.findall(r"\w+", query.lower()))
        scored: list[ScoredHit] = []

        for rank, h in enumerate(hits):
            content = h.get("content", h.get("text", ""))
            base_score = float(h.get("score", h.get("similarity", 0)))

            # Keyword overlap
            content_terms = set(re.findall(r"\w+", content.lower())) if content else set()
            overlap = len(query_terms & content_terms) / max(len(query_terms), 1) if query_terms else 0.0

            # Position bonus: earliest positions get +20% max, linearly decaying
            position_bonus = max(0.0, 1.0 - (rank / max(len(hits) * 2, 1)))

            # Composite: weighted base + keyword overlap + small position bonus
            final_score = alpha * base_score + (1.0 - alpha) * overlap * 0.8 + position_bonus * 0.05

            scored.append(
                ScoredHit(
                    content=content,
                    score=base_score,
                    metadata=h.get("metadata", {}),
                    collection=h.get("collection", ""),
                    doc_id=h.get("doc_id", h.get("id", "")),
                    keyword_overlap=overlap,
                    position_bonus=position_bonus,
                    final_score=final_score,
                )
            )

        # Sort by final score descending
        scored.sort(key=lambda s: -s.final_score)
        return scored

    def score_document(self, content: str, query: str) -> float:
        """Score a single document against a query. Returns 0-1."""
        if not content or not query:
            return 0.0
        query_terms = set(re.findall(r"\w+", query.lower()))
        content_terms = set(re.findall(r"\w+", content.lower()))
        overlap = len(query_terms & content_terms) / max(len(query_terms), 1)
        return min(1.0, overlap)
