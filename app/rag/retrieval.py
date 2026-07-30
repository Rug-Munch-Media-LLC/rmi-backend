"""Hybrid retrieval layer — query expansion, multi-pillar search, context assembly.

Builds on the existing M3 engine (engine.py) with higher-level constructs:

- ``HybridRetriever``  — weighted fusion of ANN vector + BM25-lite keyword search
- ``ContextBuilder``   — assembles retrieved chunks into structured LLM context
- ``RelevanceScorer`` — post-hoc relevance re-ranking with text features
- ``QueryExpander``   — generates additional search terms for better recall

Usage:
    retriever = HybridRetriever()
    hits = await retriever.retrieve("Was this token flagged as a honeypot?")
    ctx = ContextBuilder().build(hits)
    logger.debug(ctx.formatted)
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger

# ── Config ───────────────────────────────────────────────────────────
# Will be imported after config module is ready
from app.rag.config import (
    RAG_EXPANSION_TERMS,
    RAG_HYBRID_ALPHA,
)

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


# ── QueryExpander ────────────────────────────────────────────────────

# Simple crypto/security synonym map for query expansion.
_QUERY_SYNONYMS: dict[str, list[str]] = {
    "rug": ["rugpull", "exit_scam", "liquidity_grab"],
    "scam": ["fraud", "scheme", "fake", "malicious"],
    "honeypot": ["cannot_sell", "tax_trap", "sell_restricted"],
    "phishing": ["fake_site", "lookalike", "spoof"],
    "drain": ["drainer", "sweeper", "token_stealer"],
    "pump": ["pump_and_dump", "artificial_volume", "wash_trade"],
    "whale": ["large_holder", "concentrated_ownership", "insider"],
    "exploit": ["vulnerability", "attack", "breach", "hack"],
    "sanction": ["ofac", "blacklist", "banned", "restricted"],
    "laundry": ["mixer", "tumbler", "coinjoin", "obfuscation"],
    "flash": ["flash_loan", "uncollateralized_loan", "arbitrage"],
}

# Common crypto misspellings / variants for fuzzy expansion.
_QUERY_VARIANTS: dict[str, list[str]] = {
    "defi": ["defi", "de-fi", "decentralized_finance"],
    "nft": ["nft", "non-fungible", "nonfungible"],
    "dex": ["dex", "decentralized_exchange", "amm"],
    "cex": ["cex", "centralized_exchange"],
}


class QueryExpander:
    """Expand a user query with synonyms and related terms for better recall.

    Strategy:
      1. Tokenise the query into lowercased terms.
      2. For each known term, append synonyms from the internal map.
      3. Return the original query plus an "expanded" version with OR-groups.
    """

    def __init__(self) -> None:
        self.synonyms = _QUERY_SYNONYMS
        self.variants = _QUERY_VARIANTS

    def expand(self, query: str, max_terms: int = RAG_EXPANSION_TERMS) -> str:
        """Return an expanded query with additional search terms."""
        if not query or not query.strip():
            return query

        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", query.lower())
        extra_terms: list[str] = []
        for t in tokens:
            if t in self.synonyms:
                extra_terms.extend(self.synonyms[t])
            if t in self.variants:
                extra_terms.extend(v for v in self.variants[t] if v != t)

        # Deduplicate, limit, and append
        seen = set(tokens)
        extra = [et for et in extra_terms if et not in seen]
        expanded = query.strip()
        if extra:
            expanded += " " + " ".join(extra[:max_terms])

        log.debug("query_expanded", original_len=len(query), expanded_len=len(expanded))
        return expanded

    def expand_multi(self, queries: list[str]) -> list[str]:
        """Expand multiple queries in batch."""
        return [self.expand(q) for q in queries]


# ── RelevanceScorer ──────────────────────────────────────────────────


class RelevanceScorer:
    """Post-hoc relevance scoring with text-feature signals.

    Computes a composite score from:
      - Base similarity (from vector/keyword search)
      - Keyword overlap ratio between query and hit content
      - Position bonus (earlier results get a small boost)
    """

    def rescore(
        self,
        hits: list[dict],
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


# ── HybridRetriever ──────────────────────────────────────────────────


class HybridRetriever:
    """Weighted fusion of ANN vector search and BM25-lite keyword search.

    Delegates to the M3 engine's ``three_pillar_search`` under the hood,
    then applies query expansion and relevance re-scoring.

    Usage:
        retriever = HybridRetriever()
        hits = await retriever.retrieve(
            query="honeypot token high tax",
            collection="scanner",
            top_k=10,
        )
    """

    def __init__(self, expander: QueryExpander | None = None, scorer: RelevanceScorer | None = None) -> None:
        self._expander = expander or QueryExpander()
        self._scorer = scorer or RelevanceScorer()

    async def retrieve(
        self,
        query: str,
        collection: str = "scanner",
        top_k: int = 5,
        min_similarity: float = 0.0,
        filters: dict[str, Any] | None = None,
        expand: bool = True,
    ) -> list[ScoredHit]:
        """Run hybrid retrieval with optional query expansion.

        Args:
            query: Natural-language search query.
            collection: RAG collection to search.
            top_k: Maximum results to return.
            min_similarity: Minimum similarity threshold (0-1).
            filters: Metadata filters (exact-match).
            expand: Whether to expand the query with synonyms.

        Returns:
            List of ScoredHit, sorted by relevance.
        """
        start = time.monotonic()

        # 1. Optionally expand the query
        search_query = self._expander.expand(query) if expand else query

        # 2. Delegate to the M3 engine
        from app.rag.engine import three_pillar_search

        raw = await three_pillar_search(
            query=search_query,
            collection=collection,
            top_k=top_k,
            min_similarity=min_similarity,
            filters=filters,
        )

        # 3. Re-score and sort
        scored = self._scorer.rescore(raw, query)

        took_ms = int((time.monotonic() - start) * 1000)
        log.info(
            "hybrid_retrieve",
            collection=collection,
            top_k=top_k,
            results=len(scored),
            took_ms=took_ms,
            expanded=expand,
        )

        return scored

    async def retrieve_multi(
        self,
        query: str,
        collections: list[str] | None = None,
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> dict[str, list[ScoredHit]]:
        """Search multiple collections in parallel.

        Returns:
            Dict mapping collection name to its scored hits.
        """
        from app.rag.models import COLLECTIONS as DEFAULT_COLLECTIONS

        targets = collections or DEFAULT_COLLECTIONS
        results: dict[str, list[ScoredHit]] = {}

        for col in targets:
            try:
                hits = await self.retrieve(
                    query=query,
                    collection=col,
                    top_k=top_k,
                    min_similarity=min_similarity,
                )
                results[col] = hits
            except Exception as e:
                log.warning("multi_retrieve_failed", collection=col, error=str(e))
                results[col] = []

        return results


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
        hits: list[ScoredHit] | list[dict],
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
                        content=h.get("content", h.get("text", "")),
                        score=float(h.get("score", h.get("similarity", 0))),
                        metadata=h.get("metadata", {}),
                        collection=h.get("collection", ""),
                        doc_id=h.get("doc_id", h.get("id", "")),
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

_default_retriever = HybridRetriever()
_default_scorer = RelevanceScorer()
_default_expander = QueryExpander()
_default_builder = ContextBuilder()


async def search(
    query: str,
    collection: str = "scanner",
    top_k: int = 5,
) -> list[ScoredHit]:
    """Quick single-collection search (uses default retriever)."""
    return await _default_retriever.retrieve(
        query=query,
        collection=collection,
        top_k=top_k,
    )


def expand_query(query: str) -> str:
    """Quick query expansion (uses default expander)."""
    return _default_expander.expand(query)


def build_context(
    hits: list[ScoredHit] | list[dict],
    max_tokens: int = 4096,
) -> ContextWindow:
    """Quick context assembly (uses default builder)."""
    return _default_builder.build(hits, max_tokens=max_tokens)
