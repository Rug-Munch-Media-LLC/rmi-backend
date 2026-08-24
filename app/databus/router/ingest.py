"""DataBus Ingest — service-to-service intelligence intake (canonical route).

Revives the flow from the deprecated ``app/routers/databus_ingest.py`` on the
MOUNTED databus router package, with mandatory internal service auth.

Auth model — case-by-case service unblock:
    Internal services (rugmaps, x402, rugmunchbot, indexer, scanner, ...)
    present the fleet shared secret in the ``X-RMI-Key`` header
    (``RMI_INTERNAL_KEY``). The CSRF middleware already exempts key-bearing
    requests; this endpoint ADDITIONALLY validates the key with a
    constant-time compare, so only holders of the secret can write.
    Access is granted per service by distributing the key via
    gopass → systemd/compose env. No anonymous writes, ever.

Storage:
    Persists to the ``scraped_intel`` table (app.models.intel.ScrapedIntel)
    via the shared async session factory. Fire-and-forget semantics: 202 as
    soon as the row is committed; downstream consumers (DataBus fetch,
    RAG, scanners) read it asynchronously.

Rate limit: 10 req/s burst 20 (token bucket), keyed per service.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.database import get_db
from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger("databus.ingest")

router = APIRouter(prefix="/ingest", tags=["databus-ingest"])


# ── Auth ──────────────────────────────────────────────────────────────


def require_service_key(request: Request) -> str:
    """Validate X-RMI-Key against RMI_INTERNAL_KEY (constant-time).

    Returns the service identity string on success; raises 401 otherwise.
    """
    supplied = request.headers.get("X-RMI-Key", "")
    if not supplied or not hmac.compare_digest(supplied.encode(), INTERNAL_API_KEY.encode()):
        logger.warning(
            "databus_ingest_auth_failed path=%s has_key=%s",
            request.url.path,
            bool(supplied),
        )
        raise HTTPException(status_code=401, detail="Valid X-RMI-Key required for ingest")
    return "internal"


# ── Request / Response models ────────────────────────────────────────


class ServiceIngestRequest(BaseModel):
    """Payload from internal services (rugmaps, pryscraper, bots, ...)."""

    source: str = Field(..., min_length=1, max_length=64, description="Service identifier, e.g. rugmaps")
    data_type: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="e.g. scraped_intel, scam_report, threat_intel, blockchain_data, entity_profile, wallet_cluster",
    )
    content: dict[str, Any] = Field(..., description="The intelligence payload as a JSON object")
    url: str = Field(default="", max_length=4096, description="Source URL the data was scraped from")
    token_address: str = Field(default="", max_length=128, description="Token/entity address the data relates to")
    chain: str = Field(default="", max_length=32, description="Blockchain chain identifier")
    scraped_at: str = Field(default="", description="ISO-8601 timestamp of when the data was produced")

    model_config = {"extra": "ignore"}


class ServiceIngestResponse(BaseModel):
    """Standard ingest response."""

    success: bool
    id: int | None = None
    message: str = ""


# ── Endpoint ─────────────────────────────────────────────────────────


@router.post("")
async def ingest_intel(
    payload: ServiceIngestRequest,
    _service: str = Depends(require_service_key),
    _rl: None = Depends(get_rate_limit_dependency("databus_ingest", rate=10.0, burst=20)),
    session: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Receive intelligence from internal services.

    Fire-and-forget: returns 202 once persisted. Downstream processing
    (RAG embedding, risk scoring feedback loops) runs asynchronously.
    """
    from app.models.intel import ScrapedIntel

    scraped_dt = None
    if payload.scraped_at:
        try:
            scraped_dt = datetime.fromisoformat(payload.scraped_at.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            scraped_dt = None

    record = ScrapedIntel(
        source=payload.source,
        data_type=payload.data_type,
        content=payload.content,
        url=payload.url or None,
        token_address=payload.token_address or None,
        chain=payload.chain or None,
        scraped_at=scraped_dt,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)

    logger.info(
        "databus_ingest_accepted source=%s data_type=%s token=%s chain=%s id=%s",
        payload.source,
        payload.data_type,
        (payload.token_address or "")[:12],
        payload.chain,
        record.id,
    )
    return JSONResponse(
        status_code=202,
        content=ServiceIngestResponse(success=True, id=record.id, message="accepted").model_dump(),
    )
