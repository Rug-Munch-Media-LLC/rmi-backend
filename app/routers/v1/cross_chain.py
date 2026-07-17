"""V1 cross-chain endpoints — /api/v1/cross-chain/*.

Cross-chain wallet tracking, balance aggregation, and fund flow tracing.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/cross-chain", tags=["cross-chain-v1"])


@router.get("/{address}")
async def cross_chain_profile(address: str):
    """Get cross-chain profile for a wallet address.

    Aggregates balances, activity, and labels across all supported chains
    via app.cross_chain and app.cross_chain_correlator.
    """
    raise HTTPException(
        status_code=501,
        detail="Cross-chain profile — uses app.cross_chain + app.cross_chain_correlator. Planned for v5.1",
    )


@router.post("/trace")
async def cross_chain_trace(addresses: list[str]):
    """Trace fund flows across chains."""
    raise HTTPException(
        status_code=501,
        detail="Cross-chain fund tracing — uses app.cross_chain_trace. Planned for v5.1",
    )
