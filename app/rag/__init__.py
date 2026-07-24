"""RAG domain - v3 M3 engine surface.

Public API (per v3 unfuck guide §M3):
- RAGService, init_rag
- SearchRequest, SearchResponse, SearchHit
- IngestRequest, IngestResult
- FeedbackRecord
- EmbeddingProvider, COLLECTIONS
- config: RAG_INGESTION_BATCH_SIZE, RAG_CHUNK_SIZE, RAG_COLLECTIONS, ...
- ingestion: DocumentProcessor, QualityFilter, RateLimiter, BatchIngestor,
  IngestionScheduler, get_ingestor, ingest_scan_result, ...
- retrieval: HybridRetriever, ContextBuilder, RelevanceScorer, QueryExpander
"""

from __future__ import annotations

from app.core import health as health_mod
from app.core.health import DomainHealth


async def _health_check() -> DomainHealth:
    """RAG health: v3 engine present + Redis reachable."""
    try:
        from app.rag.engine import get_stats

        stats = get_stats()
        return DomainHealth(
            name="rag",
            healthy=True,
            details={
                "engine": "v3-m3",
                "backend": stats.get("backend", "unknown"),
                "total_docs": stats.get("total_docs", 0),
            },
        )
    except Exception as e:
        return DomainHealth(name="rag", healthy=False, error=str(e))

health_mod.register_health_check("rag", _health_check)

# Public API
# ── New pipeline exports ─────────────────────────────────────────────
# Config
from app.rag.config import (  # noqa: E402
    RAG_CHUNK_OVERLAP,
    RAG_CHUNK_SIZE,
    RAG_COLLECTIONS as PIPELINE_COLLECTIONS,
    RAG_INGESTION_BATCH_SIZE,
    RAG_INGESTION_MAX_PER_MINUTE,
    RAG_MIN_CHUNK_CHARS,
    RAG_MIN_DOC_LENGTH,
    RAG_SCHEDULER_INTERVAL,
    RAG_SPAM_REPETITION_THRESHOLD,
)

# Ingestion pipeline
from app.rag.ingestion import (  # noqa: E402
    BatchIngestor,
    DocumentProcessor,
    IngestionScheduler,
    IngestionStatus,
    IngestReport,
    QualityFilter,
    RateLimiter,
    get_ingestor,
    get_processor,
    get_rate_limiter,
    get_scheduler,
    ingest_news_article,
    ingest_research_post,
    ingest_scam_pattern,
    ingest_scan_result,
    ingest_wallet_profile,
)
from app.rag.models import (  # noqa: E402
    COLLECTIONS,
    EmbeddingProvider,
    FeedbackRecord,
    IngestRequest,
    IngestResult,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

# Retrieval
from app.rag.retrieval import (  # noqa: E402
    ContextBuilder,
    ContextWindow,
    HybridRetriever,
    QueryExpander,
    RelevanceScorer,
    ScoredHit,
    build_context,
    expand_query,
    search as hybrid_search,
)
from app.rag.service import RAGService, init_rag  # noqa: E402

__all__ = [
    "COLLECTIONS",
    "EmbeddingProvider",
    "FeedbackRecord",
    "IngestRequest",
    "IngestResult",
    "RAGService",
    "SearchHit",
    "SearchRequest",
    "SearchResponse",
    "init_rag",
    # Config
    "PIPELINE_COLLECTIONS",
    "RAG_CHUNK_OVERLAP",
    "RAG_CHUNK_SIZE",
    "RAG_INGESTION_BATCH_SIZE",
    "RAG_INGESTION_MAX_PER_MINUTE",
    "RAG_MIN_CHUNK_CHARS",
    "RAG_MIN_DOC_LENGTH",
    "RAG_SCHEDULER_INTERVAL",
    "RAG_SPAM_REPETITION_THRESHOLD",
    # Ingestion
    "BatchIngestor",
    "DocumentProcessor",
    "IngestionScheduler",
    "IngestionStatus",
    "IngestReport",
    "QualityFilter",
    "RateLimiter",
    "get_ingestor",
    "get_processor",
    "get_rate_limiter",
    "get_scheduler",
    "ingest_news_article",
    "ingest_research_post",
    "ingest_scan_result",
    "ingest_scam_pattern",
    "ingest_wallet_profile",
    # Retrieval
    "ContextBuilder",
    "ContextWindow",
    "HybridRetriever",
    "QueryExpander",
    "RelevanceScorer",
    "ScoredHit",
    "build_context",
    "expand_query",
    "hybrid_search",
]
