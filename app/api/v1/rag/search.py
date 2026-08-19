"""V1 RAG route — delegates to rugbrain HTTP API.

The RAG system has been decoupled from rmi-backend. All search and
ingest operations now route through rugbrain's HTTP API (rmi-rag).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.integrations.rugbrain_client import rugbrain_ingest, rugbrain_search

router = APIRouter(prefix="/api/v1/rag/v2", tags=["rag"])


class SearchRequest(BaseModel):
    """RAG search request."""

    query: str = Field(..., min_length=1, max_length=2048)
    collection: str = Field(default="scam_intel")
    top_k: int = Field(default=5, ge=1, le=50)


class SearchHit(BaseModel):
    """A single search result."""

    content: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)
    collection: str = ""
    doc_id: str = ""


class SearchResponse(BaseModel):
    """RAG search response."""

    query: str
    hits: list[SearchHit] = Field(default_factory=list)
    took_ms: float = 0.0
    collections_searched: list[str] = Field(default_factory=list)


class IngestRequest(BaseModel):
    """RAG ingest request."""

    collection: str = "scam_intel"
    doc_id: str | None = None
    content: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResult(BaseModel):
    """Result of an ingest operation."""

    doc_id: str = ""
    collection: str = ""
    status: str = ""
    error: str = ""


class FeedbackRecord(BaseModel):
    """Scanner feedback record — ingests known scam data."""

    token_address: str
    chain: str = ""
    risk_score: float = 0.0
    risk_flags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest) -> SearchResponse:
    """RAG search via rugbrain HTTP API."""
    t0 = datetime.now(UTC)
    results = await rugbrain_search(
        query=req.query,
        collections=[req.collection],
        limit=req.top_k,
    )
    hits = [
        SearchHit(
            content=(r.get("content") or r.get("text") or ""),
            score=r.get("rrf_score", 0),
            metadata=r.get("metadata", {}),
            collection=req.collection,
            doc_id=doc_id,
        )
        for r in results
        if (doc_id := r.get("doc_id"))
    ]
    took_ms = (datetime.now(UTC) - t0).total_seconds() * 1000
    return SearchResponse(
        query=req.query,
        hits=hits,
        took_ms=took_ms,
        collections_searched=[req.collection],
    )


@router.post("/ingest", response_model=IngestResult)
async def ingest(req: IngestRequest) -> IngestResult:
    """Ingest a document via rugbrain HTTP API."""
    ok = await rugbrain_ingest(
        content=req.content,
        collection=req.collection,
        doc_id=req.doc_id,
        metadata=req.metadata,
    )
    return IngestResult(
        doc_id=req.doc_id or "",
        collection=req.collection,
        status="ok" if ok else "failed",
    )


@router.post("/feedback", response_model=IngestResult)
async def feedback(record: FeedbackRecord) -> IngestResult:
    """Record scanner feedback — ingest known scam data into rugbrain."""
    content = (
        f"Token: {record.token_address} on {record.chain}. "
        f"Risk score: {record.risk_score}. "
        f"Flags: {', '.join(record.risk_flags)}"
    )
    ok = await rugbrain_ingest(
        content=content,
        collection="known_scams",
        doc_id=record.token_address,
        metadata={
            "token_address": record.token_address,
            "chain": record.chain,
            "risk_score": record.risk_score,
            "risk_flags": record.risk_flags,
            **(record.metadata or {}),
        },
    )
    return IngestResult(
        doc_id=record.token_address,
        collection="known_scams",
        status="ok" if ok else "failed",
    )


@router.get("/stats")
async def stats() -> dict[str, Any]:
    """Collection stats from Qdrant v2."""
    import httpx

    try:
        url = "http://rmi-qdrant-v2:6333"
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{url}/collections")
            r.raise_for_status()
            collections = r.json().get("result", {}).get("collections", [])
            result = {}
            for c in collections:
                name = c["name"]
                info = await client.get(f"{url}/collections/{name}")
                if info.status_code == 200:
                    data = info.json().get("result", {})
                    result[name] = {
                        "points": data.get("points_count", 0),
                        "vectors": data.get("vectors_count", 0),
                        "dim": (data.get("config", {}).get("params", {}).get("vectors", {}) or {}).get("size", 0),
                    }
            return {"collections": result, "status": "ok"}
    except Exception as e:
        return {"collections": {}, "status": "error", "error": str(e)}


@router.post("/bulk-ingest")
async def bulk(req: BulkIngestRequest) -> dict[str, Any]:
    """Ingest many items into a collection via rugbrain. Sequential (max 500)."""
    if not req.items:
        raise HTTPException(status_code=400, detail="items must be non-empty")
    if len(req.items) > 500:
        raise HTTPException(status_code=400, detail="bulk limit 500 per call")

    ok_count = 0
    errors: list[str] = []
    for item in req.items:
        try:
            ok = await rugbrain_ingest(
                content=item.get("content", ""),
                collection=req.collection,
                doc_id=item.get("doc_id"),
                metadata=item.get("metadata", {}),
            )
            if ok:
                ok_count += 1
            else:
                errors.append(f"failed: {item.get('doc_id', 'unknown')}")
        except Exception as e:
            errors.append(f"error: {e}")

    return {"ingested": ok_count, "total": len(req.items), "errors": errors[:10]}


class BulkIngestRequest(BaseModel):
    collection: str = "scam_intel"
    items: list[dict[str, Any]] = Field(default_factory=list)
