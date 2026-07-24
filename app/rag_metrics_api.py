"""
RAG Metrics API - expose observability data.
GET /api/v1/rag/metrics - embedding/retrieval latency, cache hit rate, errors.
"""

from __future__ import annotations
from fastapi import APIRouter, Request

from app.rag_observability import get_metrics
from typing import Any

router = APIRouter(prefix="/api/v1/rag", tags=["rag-metrics"])

@router.get("/metrics")
async def rag_metrics(request: Request) -> Any:
    """RAG system metrics - latency, cache, ingestion, errors."""
    return get_metrics()
