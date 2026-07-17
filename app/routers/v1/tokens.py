"""V1 token endpoints — /api/v1/tokens/*.

Token information, risk scoring, and analysis.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/tokens", tags=["tokens-v1"])


class TokenSummary(BaseModel):
    address: str
    chain: str
    name: str | None = None
    symbol: str | None = None
    decimals: int | None = None
    total_supply: str | None = None
    price_usd: float | None = None


@router.get("/{address}", response_model=TokenSummary)
async def get_token_info(address: str, chain: str = "ethereum"):
    """Get token summary by address.

    NOTE: Full implementation delegates to app.api.v1.public.token.
    This stub will be replaced with real handler wiring.
    """
    raise HTTPException(
        status_code=501,
        detail="Token info endpoint — implementation delegated to app.api.v1.public.token",
    )


@router.get("/{address}/risk")
async def get_token_risk(address: str, chain: str = "ethereum"):
    """Get token risk assessment."""
    raise HTTPException(
        status_code=501,
        detail="Token risk endpoint — see app.api.v1.public.token for implementation",
    )
