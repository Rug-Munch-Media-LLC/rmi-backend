"""V1 token endpoints — /api/v1/tokens/*.

Token information, risk scoring, and analysis.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.errors import ServiceUnavailableError

router = APIRouter(prefix="/tokens", tags=["tokens-v1"])

class TokenSummary(BaseModel):
    address: str
    chain: str
    name: str | None = None
    symbol: str | None = None
    decimals: int | None = None
    total_supply: str | None = None
    price_usd: float | None = None

@router.get("/{address}", response_model=TokenSummary, summary="Get Token Info", description="Fetch basic token metadata including name, symbol, decimals, total supply, and contract verification status by address and chain.")
async def get_token_info(address: str, chain: str = "ethereum") -> None:
    """Get token summary by address.

    NOTE: Full implementation delegates to app.api.v1.public.token.
    This stub will be replaced with real handler wiring.
    """
    raise ServiceUnavailableError(
        "Token info endpoint — planned: GET /api/v1/token/{address} — see roadmap",
    )

@router.get("/{address}/risk", summary="Get Token Risk", description="Get token risk assessment with security analysis, rugpull indicators, liquidity health, and community reports. Supports all EVM chains and Solana.")
async def get_token_risk(address: str, chain: str = "ethereum") -> None:
    """Get token risk assessment."""
    raise ServiceUnavailableError(
        "Token risk endpoint — planned: GET /api/v1/token/{address}/risk — see roadmap",
    )
