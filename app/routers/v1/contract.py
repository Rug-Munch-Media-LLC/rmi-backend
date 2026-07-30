"""V1 contract endpoints — /api/v1/contract/*.

Smart contract analysis, verification, and risk assessment.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.errors import ServiceUnavailableError

router = APIRouter(prefix="/contract", tags=["contract-v1"])


@router.get(
    "/{address}",
    summary="Get Contract",
    description="Get contract details by address. Includes bytecode hash, compiler version, verification status, and known vulnerability matches.",
)
async def get_contract_info(
    address: str,
    chain: str = Query("ethereum", description="Blockchain to query"),
) -> None:
    """Get contract information and analysis.

    Returns contract type, verification status, risk flags.
    """
    raise ServiceUnavailableError(
        "Contract info endpoint — planned: GET /api/v1/contract/{address} — see roadmap",
    )


@router.get(
    "/{address}/verify",
    summary="Verify Contract",
    description="Verify a contract's source code against its on-chain bytecode. Supports multiple compiler versions and optimization settings.",
)
async def verify_contract(address: str, chain: str = "ethereum") -> None:
    """Verify contract source code."""
    raise ServiceUnavailableError(
        "Contract verification — planned: GET /api/v1/contract/{address}/verify — see roadmap",
    )
