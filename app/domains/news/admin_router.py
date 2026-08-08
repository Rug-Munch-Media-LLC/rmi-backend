"""T28 RSS Ingest HTTP endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.domains.news.ingest import ingest_all

router = APIRouter(prefix="/api/v1/news/_admin", tags=["news-admin"])


@router.post("/ingest")
async def trigger_ingest() -> dict[str, Any]:
    """Trigger RSS ingest now (synchronous). Returns counts."""
    result = await ingest_all()
    return result
