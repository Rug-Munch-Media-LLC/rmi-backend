"""DataBus Ingest Router — receive scraped intelligence from Pryscraper.

DEPRECATED — NOT MOUNTED. This router is not included in
``app.mount.ROUTER_MODULES`` and is not reachable at runtime. DataBus
ingest is handled by the canonical ``app.databus.router`` (webhooks,
cache, admin, convenience, premium). Kept in-tree only as a historical
reference; do not wire it into the app.

Endpoint:
  POST /api/v1/databus/ingest

Accepts scraped data from Pryscraper, validates it, stores it in Postgres,
and optionally forwards to the RAG pipeline for embedding.

Design:
  - Fire-and-forget: returns 202 immediately after storing
  - No synchronous dependency on RAG or downstream systems
  - Validates required fields, sanitizes content
  - Rate-limited: 10 req/s burst 20
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency

logger = get_logger("databus.ingest")

router = APIRouter(prefix="/api/v1/databus", tags=["databus"])


# ── Request / Response models ────────────────────────────────────────


class ScrapedIngestRequest(BaseModel):
    """Payload from Pryscraper (or any external scraper)."""

    source: str = Field(..., min_length=1, max_length=64, description="Data source identifier, e.g. pryscraper")
    data_type: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Type of scraped data: scraped_intel, scam_report, threat_intel, blockchain_data, entity_profile",
    )
    content: dict[str, Any] = Field(..., description="The scraped content payload as a JSON object")
    url: str = Field("", max_length=4096, description="Source URL the data was scraped from")
    token_address: str = Field("", max_length=128, description="Token/entity address the data relates to")
    chain: str = Field("", max_length=32, description="Blockchain chain identifier")
    scraped_at: str = Field("", description="ISO-8601 timestamp of when the data was scraped")

    @field_validator("content")
    @classmethod
    def content_not_empty(cls, v: dict[str, Any]) -> dict[str, Any]:
        if not v:
            raise ValueError("content must be a non-empty object")
        return v


class ScrapedIngestResponse(BaseModel):
    """Standard ingest response."""

    success: bool
    id: int | None = None
    message: str = ""
    rag_indexed: bool = False


# ── Endpoints ────────────────────────────────────────────────────────


@router.post("/ingest")
async def ingest_scraped_intel(
    request: ScrapedIngestRequest,
    _rl: None = Depends(get_rate_limit_dependency("databus_ingest", rate=10.0, burst=20)),
) -> ScrapedIngestResponse:
    """Receive scraped intelligence from Pryscraper.

    Validates the payload, stores it in the scraped_intel table, and
    optionally enqueues it for RAG embedding. Fire-and-forget semantics:
    returns 202 immediately; downstream processing (RAG) runs asynchronously.

    Rate limit: 10 req/s burst 20 (token-bucket).
    """
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from app.models.intel import ScrapedIntel

        # Parse scraped_at timestamp
        scraped_dt = None
        if request.scraped_at:
            try:
                scraped_dt = datetime.fromisoformat(request.scraped_at.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                scraped_dt = datetime.now(UTC)

        # Store in Postgres
        import os

        db_url = os.getenv("DATABASE_URL", "postgresql://localhost:5432/rmi")
        engine = create_engine(db_url)
        with Session(engine) as session:
            record = ScrapedIntel(
                source=request.source,
                data_type=request.data_type,
                content=request.content,
                url=request.url or None,
                token_address=request.token_address or None,
                chain=request.chain or None,
                scraped_at=scraped_dt,
            )
            session.add(record)
            session.commit()
            record_id = record.id
            session.refresh(record)

        logger.info(
            "scraped_intel_ingested",
            id=record_id,
            source=request.source,
            data_type=request.data_type,
            token_address=request.token_address or "none",
        )

        # ── Optional: forward to RAG (non-blocking) ──
        rag_indexed = False
        if request.data_type in ("scraped_intel", "scam_report", "threat_intel"):
            try:
                _forward_to_rag(request, record_id)
                rag_indexed = True
            except Exception as exc:
                logger.warning("rag_forward_failed", id=record_id, error=str(exc)[:200])

        return ScrapedIngestResponse(
            success=True,
            id=record_id,
            message=f"Scraped {request.data_type} from {request.source} ingested as #{record_id}",
            rag_indexed=rag_indexed,
        )

    except Exception as exc:
        logger.error("ingest_failed", source=request.source, error=str(exc)[:500])
        return ScrapedIngestResponse(
            success=False,
            message=f"Ingest failed: {str(exc)[:300]}",
        )


def _forward_to_rag(request: ScrapedIngestRequest, record_id: int) -> None:
    """Optionally forward high-value data to RAG.

    Runs synchronously in the request thread — if this becomes a bottleneck,
    move to a background task queue (Celery / arq).
    """
    try:
        import json
        import os

        import httpx

        rag_url = os.getenv("RAG_INGEST_URL", "http://localhost:8000/api/v1/rag/ingest-news")

        # Build a RAG-friendly document from the scraped content
        content_text = json.dumps(request.content, default=str)[:5000]

        payload = {
            "title": f"[{request.data_type}] {request.url or 'scraped'}",
            "body": content_text,
            "source_url": request.url or "",
            "source_name": request.source,
            "published_at": request.scraped_at or "",
            "categories": [request.data_type, "scraped_intel"],
        }

        with httpx.Client(timeout=5) as client:
            resp = client.post(rag_url, json=payload)
            if resp.status_code < 400:
                logger.info("rag_forwarded", id=record_id, status=resp.status_code)
            else:
                logger.warning(
                    "rag_forward_failed",
                    id=record_id,
                    status=resp.status_code,
                    body=resp.text[:200],
                )
    except Exception as exc:
        logger.debug("rag_forward_exception", id=record_id, error=str(exc)[:200])
