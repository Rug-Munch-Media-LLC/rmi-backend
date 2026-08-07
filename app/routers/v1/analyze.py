"""V1 analyze endpoints — /api/v1/analyze/*.

Token/contract analysis. Redirects to existing handlers.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.errors import ServiceUnavailableError

router = APIRouter(prefix="/analyze", tags=["analyze-v1"])


@router.get("/{address}", deprecated=True)
async def analyze_address(
    address: str,
    chain: str = Query("ethereum", description="Blockchain to query"),
) -> None:
    """Analyze a contract or token address.

    DEPRECATED — always returns 503. The canonical implementation lives at
    /api/v1/contract-analyzer/analyze/{address} (app.routers.contract_analyzer).
    This stub is retained so the legacy path keeps returning a structured
    error instead of 404; remove once all callers migrate.
    """
    raise ServiceUnavailableError(
        "Analyze endpoint — re-routed to /api/v1/contract-analyzer/analyze/{address} — see roadmap",
    )
