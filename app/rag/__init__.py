"""RAG domain — all operations now route through rugbrain HTTP API.

This module is kept for backward import compatibility. All actual
RAG functionality lives in rugbrain (rugbrain repo). Thin wrappers
in ``app.rag.compat``, ``app.rag.intelligence``, ``app.rag.service``,
etc. route through ``app.integrations.rugbrain_client``.
"""

from __future__ import annotations

from app.core import health as health_mod
from app.core.health import DomainHealth


async def _health_check() -> DomainHealth:
    """RAG health: rugbrain HTTP API reachable."""
    try:
        import os

        import httpx

        api_key = os.getenv("RMI_RAG_API_KEY", "")
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(
                "http://rugbrain-api:8012/api/v1/health",
                headers={"X-API-Key": api_key} if api_key else {},
            )
            healthy = r.status_code == 200
        return DomainHealth(
            name="rag",
            healthy=healthy,
            details={"backend": "rugbrain-http", "status_code": r.status_code},
        )
    except Exception as e:
        return DomainHealth(name="rag", healthy=False, error=str(e))


health_mod.register_health_check("rag", _health_check)

# ── Backward-compatible exports ──────────────────────────────────────────
# These are re-exported for callers that do ``from app.rag import X``.
# The actual implementations are in the submodules below.

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
