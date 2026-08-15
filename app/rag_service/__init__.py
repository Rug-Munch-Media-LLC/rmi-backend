"""
RAG Ingestion & Retrieval Service v4 (DEPRECATED)
=====================================================
⚠️  DEPRECATED — Use ``app.rag`` (v3 M3 engine) instead.

This module is kept for backward compatibility. All new code should
import from ``app.rag`` which provides the same public API surface
with better performance, proper error handling, and active maintenance.

Migration guide:
  - ``search_similar``        → ``app.rag.hybrid_search()``
  - ``search_multi_collection`` → ``app.rag.HybridRetriever().retrieve_multi()``
  - ``three_pillar_search``   → ``app.rag.engine.three_pillar_search()``
  - ``ingest_document``        → ``app.rag.ingestion.get_ingestor().ingest()``
  - ``detect_scam_patterns``   → ``app.rag.retrieval.HybridRetriever().retrieve()``
  - ``get_embedder``           → ``app.rag.embedder.get_default_embedder()``

This shim will be removed in a future release (target: Q1 2027).
"""

# DEPRECATED — will be removed 2026-08-15. Import from app.rag directly or use rmi-rag-api.

from __future__ import annotations

import warnings
from typing import Any

warnings.warn(
    "app.rag_service is deprecated. Use app.rag (v3 M3 engine) instead. See docstring for migration guide.",
    DeprecationWarning,
    stacklevel=2,
)

from app.core.logging import get_logger  # noqa: E402 — module docstring precedes import block
from app.crypto_embeddings import (  # noqa: E402 — module docstring precedes import block
    COLLECTIONS,
    KNOWN_SCAM_PATTERNS,
    CryptoEmbedder,
    EmbeddingResult,
    extract_contract_features,
    get_embedder,
)

logger = get_logger(__name__)

# Re-exports
from app.rag_service import state  # noqa: E402 — module docstring precedes import block
from app.rag_service.cache import _get_redis  # noqa: E402 — module docstring precedes import block
from app.rag_service.detection import (  # noqa: E402 — module docstring precedes import block
    detect_scam_patterns,
    get_goplus_analysis,
)
from app.rag_service.indexing import (  # noqa: E402 — module docstring precedes import block
    _embed_by_collection,
    _preembed_scam_patterns,
    ingest_document,
    ingest_forensic_report,
    seed_known_scams,
)
from app.rag_service.retrieval import (  # noqa: E402 — module docstring precedes import block
    _classify_query,
    _enrich_ann_results,
    _rrf_fuse,
    _sparse_text_search,
    search_multi_collection,
    search_similar,
    search_with_transform,
    three_pillar_search,
)
from app.rag_service.stats import (  # noqa: E402 — module docstring precedes import block
    get_stats,
)


def __getattr__(name: str) -> Any:
    """Forward shared-state names to app.rag_service.state (PEP 562).

    The module-level duplicates were removed 2026-08-15; the live state lives in
    state.py and is mutated there (indexing.py, detection.py, cache.py). A plain
    ``from app.rag_service.state import X`` re-export would snapshot immutable
    values (bools/None) at import time, so forward attribute access instead.
    """
    if name in {"_seeded", "_pattern_cache", "_pattern_cache_loaded", "_redis_pool"}:
        return getattr(state, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "COLLECTIONS",
    "KNOWN_SCAM_PATTERNS",
    "CryptoEmbedder",
    "EmbeddingResult",
    "_classify_query",
    "_embed_by_collection",
    "_enrich_ann_results",
    "_get_redis",
    "_pattern_cache",
    "_pattern_cache_loaded",
    "_preembed_scam_patterns",
    "_redis_pool",
    "_rrf_fuse",
    "_seeded",
    "_sparse_text_search",
    "detect_scam_patterns",
    "extract_contract_features",
    "get_embedder",
    "get_goplus_analysis",
    "get_stats",
    "ingest_document",
    "ingest_forensic_report",
    "search_multi_collection",
    "search_similar",
    "search_with_transform",
    "seed_known_scams",
    "three_pillar_search",
]
