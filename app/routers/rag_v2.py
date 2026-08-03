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

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.rag.graph import create_crypto_rag_graph

router = APIRouter(prefix="/api/v1/rag/v2", tags=["rag"])


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


@router.post("/ask", response_model=AskResponse)
async def ask_crypto_question(request: AskRequest) -> AskResponse:
    """Run the agentic research graph over a crypto question."""
    graph = create_crypto_rag_graph()
    result = await graph.ainvoke({"query": request.query})
    sources = list(result.get("sources") or [])
    return AskResponse(
        answer=result.get("answer", ""),
        query_type=result.get("query_type", "general"),
        sources=[s for s in sources if s != "__unverified__"],
        tool_calls=result.get("tool_calls") or [],
        verified="__unverified__" not in sources,
    )


__all__ = ["AskRequest", "AskResponse", "router"]
