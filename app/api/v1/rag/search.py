"""V1 RAG route - thin HTTP layer over app.rag.

The RAG system is the most coupled module (14 legacy files). This
facade exposes the most-used operations: search, ingest, feedback.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.rag import (
    FeedbackRecord,
    IngestRequest,
    IngestResult,
    RAGService,
    SearchRequest,
    SearchResponse,
)
from app.rag.engine import bulk_ingest as engine_bulk_ingest
from app.rag.engine import get_stats as engine_get_stats

router = APIRouter(prefix="/api/v1/rag/v2", tags=["rag"])


def _service() -> RAGService:
    return RAGService()


class BulkIngestRequest(BaseModel):
    collection: str = "scam_intel"
    items: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/search", response_model=SearchResponse)
async def search(
    req: SearchRequest,
    svc: Annotated[RAGService, Depends(_service)],
) -> SearchResponse:
    """RAG search. Returns Pydantic response with hits + scores."""
    return await svc.search(req)


@router.post("/ingest", response_model=IngestResult)
async def ingest(
    req: IngestRequest,
    svc: Annotated[RAGService, Depends(_service)],
) -> IngestResult:
    """Ingest a document into the RAG system."""
    return await svc.ingest(req)


@router.post("/feedback", response_model=IngestResult)
async def feedback(
    record: FeedbackRecord,
    svc: Annotated[RAGService, Depends(_service)],
) -> IngestResult:
    """Record scanner → RAG feedback. Ingests known scam into known_scams collection."""
    ok = await svc.record_feedback(record)
    return IngestResult(
        doc_id=record.token_address,
        collection="known_scams",
        status="ok" if ok else "failed",
    )


@router.get("/stats")
async def stats() -> dict:
    """Per-collection vector counts + active embedder backend."""
    return engine_get_stats()


@router.post("/bulk-ingest")
async def bulk(req: BulkIngestRequest) -> dict:
    """Ingest many items into a collection sequentially (max 500 per call)."""
    if not req.items:
        raise HTTPException(status_code=400, detail="items must be non-empty")
    if len(req.items) > 500:
        raise HTTPException(status_code=400, detail="bulk limit 500 per call")
    return await engine_bulk_ingest(items=req.items, collection=req.collection)
