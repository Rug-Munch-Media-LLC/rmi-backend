"""V1 deployer endpoints — /api/v1/deployer/*.

Deployer reputation, history, and linked addresses.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/deployer", tags=["deployer-v1"])


@router.get("/{address}")
async def get_deployer_info(
    address: str,
    chain: str = Query("ethereum", description="Blockchain to query"),
):
    """Get deployer information and reputation.

    Returns deployer history, deployed contracts, risk score.
    """
    raise HTTPException(
        status_code=501,
        detail="Deployer info endpoint — planned for v5.1. See app.deployer_reputation",
    )


@router.get("/{address}/contracts")
async def get_deployer_contracts(
    address: str,
    chain: str = Query("ethereum", description="Blockchain to query"),
):
    """Get contracts deployed by this address."""
    raise HTTPException(
        status_code=501,
        detail="Deployer contracts endpoint — planned for v5.1",
    )
