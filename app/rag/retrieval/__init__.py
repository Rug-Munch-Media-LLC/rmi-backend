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

Split into sub-modules: search (retriever/expansion), rerank (scoring),
filters (news boosts/entity extraction), helpers (types/context assembly).
"""

from app.rag.retrieval.filters import (
    apply_news_boosts,
    extract_crypto_entities,
)
from app.rag.retrieval.helpers import (
    ContextBuilder,
    ContextWindow,
    ScoredHit,
    build_context,
)
from app.rag.retrieval.rerank import RelevanceScorer
from app.rag.retrieval.search import (
    HybridRetriever,
    QueryExpander,
    _qdrant_search,
    expand_query,
    search,
)

__all__ = [
    "ContextBuilder",
    "ContextWindow",
    "HybridRetriever",
    "QueryExpander",
    "RelevanceScorer",
    "ScoredHit",
    "_qdrant_search",
    "apply_news_boosts",
    "build_context",
    "expand_query",
    "extract_crypto_entities",
    "search",
]
