"""Tracing router — /api/v1/tracing/*.

Transaction tracing pipeline: decode receipt logs via 4byte, map wallet-to-
wallet hops, and build a cluster graph for RugMaps visualization.

    POST /api/v1/tracing/decode   {tx_hash, chain} → {logs, wallet_hops, graph}
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.errors import AppError, ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/tracing", tags=["tracing"])


class TraceDecodeRequest(BaseModel):
    """Request contract for POST /api/v1/tracing/decode."""

    tx_hash: str = Field(..., description="0x-prefixed transaction hash (64 hex chars)")
    chain: str = Field(default="ethereum", description="EVM chain name (ethereum, base, polygon, ...)")


@router.post(
    "/decode",
    summary="Decode transaction tracing",
    description="Fetch receipt logs for a transaction, decode event signatures via "
    "the 4byte directory, sort by logIndex, map wallet-to-wallet hops, and build "
    "a cluster graph for RugMaps visualization.",
)
async def trace_decode(
    req: TraceDecodeRequest,
    include_trace: bool = Query(False, description="Optionally attempt debug_traceTransaction internal calls"),
) -> dict[str, Any]:
    """Decode a single transaction into logs, wallet hops, and a cluster graph."""
    from app.domains.scanner.tracing import decode_transaction

    try:
        result = await decode_transaction(req.tx_hash, req.chain, include_trace=include_trace)
        return result.model_dump(by_alias=True)
    except ValidationError as e:
        raise e
    except Exception as e:
        logger.warning("tracing_decode_failed tx=%s chain=%s err=%s", req.tx_hash[:18], req.chain, e)
        raise AppError(message=str(e)[:500]) from e


@router.get("/health", summary="Tracing health")
async def tracing_health() -> dict[str, str]:
    """Health check for the tracing pipeline."""
    return {"status": "ok", "service": "tracing-pipeline"}
