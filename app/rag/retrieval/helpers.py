"""Shared data types and context assembly for the retrieval package."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

log = get_logger(__name__)

# ── Data types ───────────────────────────────────────────────────────


@dataclass
class ScoredHit:
    """A search hit with additional relevance signals."""

    content: str
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    collection: str = ""
    doc_id: str = ""
    keyword_overlap: float = 0.0
    position_bonus: float = 0.0
    final_score: float = 0.0


@dataclass
class ContextWindow:
    """Assembled context ready for LLM consumption."""

    formatted: str = ""
    hits: list[ScoredHit] = field(default_factory=list)
    total_tokens_est: int = 0
    sources: list[str] = field(default_factory=list)
    took_ms: int = 0


# ── ContextBuilder ───────────────────────────────────────────────────

# Estimate: 1 token ≈ 4 characters for English text
_CHARS_PER_TOKEN: int = 4


class ContextBuilder:
    """Assemble retrieved chunks into structured LLM context.

    Produces a ``ContextWindow`` with:
      - A formatted string (sources with metadata, ranked by score)
      - Source tracking for citation
      - Token estimation for context window managment
    """

    def build(
        self,
        hits: list[ScoredHit] | list[dict[str, Any]],
        max_tokens: int = 4096,
        include_metadata: bool = True,
    ) -> ContextWindow:
        """Assemble hits into a single context window.

        Args:
            hits: Retrieved hits (ScoredHit objects or raw dicts).
            max_tokens: Maximum estimated token budget for the context.
            include_metadata: Include collection/source info in formatting.

        Returns:
            ContextWindow with formatted text and metadata.
        """
        start = time.monotonic()

        # Normalise to ScoredHit if raw dicts
        normalised: list[ScoredHit] = []
        for h in hits:
            if isinstance(h, ScoredHit):
                normalised.append(h)
            elif isinstance(h, dict):
                normalised.append(
                    ScoredHit(
                        content=h.get("content", h.get("text", "")) or "",
                        score=float(h.get("score", h.get("similarity", 0)) or 0),
                        metadata=h.get("metadata", {}) or {},
                        collection=h.get("collection", "") or "",
                        doc_id=h.get("doc_id", h.get("id", "")) or "",
                    )
                )

        # Sort by score descending
        normalised.sort(key=lambda s: -s.final_score if s.final_score else -s.score)

        parts: list[str] = []
        sources: list[str] = []
        total_chars = 0
        max_chars = max_tokens * _CHARS_PER_TOKEN

        for hit in normalised:
            # Estimate this hit's contribution
            estimated_chars = len(hit.content) + 200  # + metadata overhead
            if total_chars + estimated_chars > max_chars:
                break

            if include_metadata:
                tags = {
                    "source": hit.collection,
                    "score": round(hit.final_score if hit.final_score else hit.score, 3),
                }
                if hit.doc_id:
                    tags["id"] = hit.doc_id
                parts.append(
                    f"[Document from {hit.collection}]\n"
                    f"{hit.content}\n"
                    f"(score={tags['score']}, id={tags.get('id', '—')})\n"
                )
            else:
                parts.append(hit.content)

            total_chars += estimated_chars

            if hit.collection:
                sources.append(hit.collection)

        formatted = "\n---\n".join(parts)
        took_ms = int((time.monotonic() - start) * 1000)

        return ContextWindow(
            formatted=formatted,
            hits=normalised,
            total_tokens_est=total_chars // _CHARS_PER_TOKEN,
            sources=list(set(sources)),
            took_ms=took_ms,
        )

    @staticmethod
    def to_prompt(context: ContextWindow, question: str, system_prompt: str = "") -> str:
        """Render context + question into a prompt string."""
        parts = []
        if system_prompt:
            parts.append(system_prompt)
        parts.append("Relevant context:")
        parts.append(context.formatted)
        parts.append(f"Question: {question}")
        return "\n\n".join(parts)


# ── Convenience instance ─────────────────────────────────────────────

_default_builder = ContextBuilder()


def build_context(
    hits: list[ScoredHit] | list[dict[str, Any]],
    max_tokens: int = 4096,
) -> ContextWindow:
    """Quick context assembly (uses default builder)."""
    return _default_builder.build(hits, max_tokens=max_tokens)
