"""V1 analyze endpoints — /api/v1/analyze/*.

Token/contract analysis. Redirects to existing handlers.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/analyze", tags=["analyze-v1"])


@router.get("/{address}")
async def analyze_address(
    address: str,
    chain: str = Query("ethereum", description="Blockchain to query"),
):
    """Analyze a contract or token address.

    Full implementation at /api/v1/contract-analyzer/analyze/{address}
    (mounted from app.routers.contract_analyzer).
    """
    raise HTTPException(
        status_code=501,
        detail="Analyze endpoint — see /api/v1/contract-analyzer/analyze/{address}",
    )
