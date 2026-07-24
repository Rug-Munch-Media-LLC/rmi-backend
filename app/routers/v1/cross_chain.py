"""V1 cross-chain endpoints — /api/v1/cross-chain/*.

Cross-chain wallet tracking, balance aggregation, and fund flow tracing.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import ServiceUnavailableError

router = APIRouter(prefix="/cross-chain", tags=["cross-chain-v1"])


@router.get("/{address}")
async def cross_chain_profile(address: str) -> None:
    """Get cross-chain profile for a wallet address.

    Aggregates balances, activity, and labels across all supported chains
    via app.cross_chain and app.cross_chain_correlator.
    """
    raise ServiceUnavailableError(
        "Cross-chain profile — planned: GET /api/v1/cross-chain/{address} — see roadmap",
    )


@router.post("/trace", summary="Cross Chain Trace", description="Trace fund flows across multiple blockchains. Follows transactions through bridges, mixers, and CEX deposits. Returns complete flow visualization.")
async def cross_chain_trace(addresses: list[str]) -> None:
    """Trace fund flows across chains."""
    raise ServiceUnavailableError(
        "Cross-chain fund tracing — planned: POST /api/v1/cross-chain/trace — see roadmap",
    )
