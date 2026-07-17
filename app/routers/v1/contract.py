"""V1 contract endpoints — /api/v1/contract/*.

Smart contract analysis, verification, and risk assessment.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/contract", tags=["contract-v1"])


@router.get("/{address}")
async def get_contract_info(
    address: str,
    chain: str = Query("ethereum", description="Blockchain to query"),
):
    """Get contract information and analysis.

    Returns contract type, verification status, risk flags.
    """
    raise HTTPException(
        status_code=501,
        detail="Contract info endpoint — uses app.contract_deepscan. Planned for v5.1",
    )


@router.get("/{address}/verify")
async def verify_contract(address: str, chain: str = "ethereum"):
    """Verify contract source code."""
    raise HTTPException(
        status_code=501,
        detail="Contract verification — uses Sourcify/Etherscan APIs. Planned for v5.1",
    )
