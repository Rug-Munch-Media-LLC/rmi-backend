"""RAG v2 router — agentic graph endpoint.

Wires the LangGraph research orchestrator (``app.rag.graph``) behind a
thin HTTP surface. The previous RAG v2 facade (``app.api.v1.rag.search``)
remains for raw search/ingest; this router adds the agentic ``/ask``
flow: classify → retrieve → rerank → synthesize → verify.

Usage:
    POST /api/v1/rag/v2/ask
    {"query": "is this token a honeypot?"}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core.rate_limiter import get_rate_limit_dependency
from app.rag.graph import _UNVERIFIED_SENTINEL, create_crypto_rag_graph

router = APIRouter(prefix="/api/v1/rag/v2", tags=["rag"])

# Compiled LangGraph is expensive to build; cache it at module level so every
# /ask request reuses the same state machine instead of recompiling.
_rag_graph: Any | None = None


def get_rag_graph() -> Any:
    """Return the cached compiled research graph (build once, reuse forever)."""
    global _rag_graph
    if _rag_graph is None:
        _rag_graph = create_crypto_rag_graph()
    return _rag_graph


class AskRequest(BaseModel):
    """Agentic RAG question."""

    query: str = Field(..., min_length=1, max_length=2048, description="Crypto research question.")


class AskResponse(BaseModel):
    """Agentic RAG answer."""

    answer: str
    query_type: str = "general"
    sources: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    verified: bool = True


class EvaluateRequest(BaseModel):
    """Ragas evaluation request for the RAG pipeline."""

    test_set: list[dict[str, Any]] | None = Field(
        default=None,
        description="Custom test set: [{question, ground_truth}]. Defaults to the RugBrain golden set.",
    )
    collection: str | None = Field(
        default=None,
        description="Collection to evaluate against (default: threat_intel_v2).",
    )


@router.post("/ask", response_model=AskResponse)
async def ask_crypto_question(
    request: AskRequest,
    _rl: None = Depends(get_rate_limit_dependency("ai")),
) -> AskResponse:
    """Run the agentic research graph over a crypto question."""
    graph = get_rag_graph()
    result = await graph.ainvoke({"query": request.query})
    sources = list(result.get("sources") or [])
    return AskResponse(
        answer=result.get("answer", ""),
        query_type=result.get("query_type", "general"),
        sources=[s for s in sources if s != _UNVERIFIED_SENTINEL],
        tool_calls=result.get("tool_calls") or [],
        verified=_UNVERIFIED_SENTINEL not in sources,
    )


@router.post("/evaluate")
async def evaluate_rag(request: EvaluateRequest) -> dict[str, Any]:
    """Run Ragas evaluation on the RAG pipeline.

    Lazy-imports ``app.rag.evaluation`` so the router stays importable even
    when ragas is not installed (evaluation falls back to heuristics).
    """
    from app.rag.evaluation import evaluate_rag_pipeline, record_evaluation_results

    result = await evaluate_rag_pipeline(
        test_set=request.test_set,
        collection=request.collection or "threat_intel_v2",
    )
    record_evaluation_results(result)
    return result


__all__ = ["AskRequest", "AskResponse", "EvaluateRequest", "router"]
