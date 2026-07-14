"""T11 - Federated labels router.

Endpoint:
  GET /api/v1/labels/{address}?chain=ethereum

Returns:
  {
    "address": "0x...",
    "chain": "ethereum",
    "labels": [...],          # list of label dicts
    "sources_queried": [...], # which sources worked
    "total": N,
  }
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.domains.labels import get_federated_api

log = get_logger(__name__)

router = APIRouter(prefix="/labels", tags=["labels"])


class LabelsResponse(BaseModel):
    """Response for GET /api/v1/labels/{address}."""

    address: str
    chain: str
    labels: list[dict[str, Any]]
    sources_queried: list[str]
    total: int


@router.get("/{address}", response_model=LabelsResponse)
async def get_labels(
    address: str,
    chain: str = Query(
        "ethereum",
        description="Blockchain (ethereum, solana, base, arbitrum, etc.)",
    ),
    use_cache: bool = Query(True, description="Use Redis cache (1h TTL)"),
    include_low_confidence: bool = Query(False, description="Include labels with confidence < 0.3"),
) -> LabelsResponse:
    """Get all wallet labels from 6 federated sources in parallel.

    Sources queried:
      1. eth-labels.db         - 115K local labels (SQLite)
      2. Etherscan CSV         - 51K labels (DuckDB)
      3. Ethereum labels CSV   - 51K labels (DuckDB)
      4. Solana labels CSV     - 103K labels (DuckDB)
      5. ClickHouse            - wallet_memory.wallet_labels
      6. MetaSleuth            - BlockSec AML API

    Graceful degradation: failed sources are skipped, not raised.
    """
    if not address or len(address) < 8:
        raise HTTPException(
            status_code=400,
            detail=f"Address too short (got {len(address)} chars, need >= 8)",
        )

    try:
        api = get_federated_api()
        result = await api.get_labels_dict(
            address,
            chain=chain,
            use_cache=use_cache,
            include_low_confidence=include_low_confidence,
        )
        return LabelsResponse(**result)
    except Exception as e:
        log.exception("labels_endpoint_fail address=%s", address[:10])
        raise HTTPException(
            status_code=500,
            detail=f"Label fetch failed: {type(e).__name__}: {str(e)[:200]}",
        ) from e
