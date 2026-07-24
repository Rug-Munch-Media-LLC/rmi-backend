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
from __future__ import annotations
import warnings
warnings.warn(
    "app.rag_service is deprecated. Use app.rag (v3 M3 engine) instead. "
    "See docstring for migration guide.",
    DeprecationWarning,
    stacklevel=2,
)
import warnings
warnings.warn(
    "app.rag_service is deprecated. Use app.rag (v3 M3 engine) instead. "
    "See docstring for migration guide.",
    DeprecationWarning,
    stacklevel=2,
)

from app.crypto_embeddings import (
    COLLECTIONS,
    KNOWN_SCAM_PATTERNS,
    CryptoEmbedder,
    EmbeddingResult,
    extract_contract_features,
    get_embedder,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

# Shared state
_seeded = False
_pattern_cache: dict[str, EmbeddingResult] = {}
_pattern_cache_loaded = False
_redis_pool = None

# Re-exports
from app.rag_service.cache import _get_redis  # noqa: E402
from app.rag_service.indexing import (  # noqa: E402
    _embed_by_collection,
    _preembed_scam_patterns,
    ingest_document,
    ingest_forensic_report,
    seed_known_scams,
)
from app.rag_service.retrieval import (  # noqa: E402
    _classify_query,
    _enrich_ann_results,
    _rrf_fuse,
    _sparse_text_search,
    search_multi_collection,
    search_similar,
    search_with_transform,
    three_pillar_search,
)
from app.rag_service.detection import (  # noqa: E402
    detect_scam_patterns,
    get_goplus_analysis,
)
from app.rag_service.stats import (  # noqa: E402
    get_stats,
)

__all__ = [
    "COLLECTIONS", "KNOWN_SCAM_PATTERNS", "CryptoEmbedder", "EmbeddingResult",
    "extract_contract_features", "get_embedder",
    "_pattern_cache", "_pattern_cache_loaded", "_redis_pool", "_seeded",
    "_get_redis",
    "_embed_by_collection", "_preembed_scam_patterns",
    "ingest_document", "ingest_forensic_report", "seed_known_scams",
    "_classify_query", "_enrich_ann_results", "_rrf_fuse",
    "_sparse_text_search", "search_multi_collection", "search_similar",
    "search_with_transform", "three_pillar_search",
    "detect_scam_patterns", "get_goplus_analysis",
    "get_stats",
]
