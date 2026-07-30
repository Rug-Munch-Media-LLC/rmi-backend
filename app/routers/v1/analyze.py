"""V1 analyze endpoints — /api/v1/analyze/*.

Token/contract analysis. Redirects to existing handlers.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.errors import ServiceUnavailableError

router = APIRouter(prefix="/analyze", tags=["analyze-v1"])


@router.get("/{address}")
async def analyze_address(
    address: str,
    chain: str = Query("ethereum", description="Blockchain to query"),
) -> None:
    """Analyze a contract or token address.

    Full implementation at /api/v1/contract-analyzer/analyze/{address}
    (mounted from app.routers.contract_analyzer).
    """
    raise ServiceUnavailableError(
        "Analyze endpoint — re-routed to /api/v1/contract-analyzer/analyze/{address} — see roadmap",
    )
