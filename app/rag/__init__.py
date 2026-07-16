"""RAG domain - v3 M3 engine surface.

Public API (per v3 unfuck guide §M3):
- RAGService, init_rag
- SearchRequest, SearchResponse, SearchHit
- IngestRequest, IngestResult
- FeedbackRecord
- EmbeddingProvider, COLLECTIONS
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
]
