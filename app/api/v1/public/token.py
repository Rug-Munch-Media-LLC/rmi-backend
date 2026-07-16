"""Public token endpoints - /api/v1/token/*.

Stub for unauthenticated token queries. Real implementation will
fetch token metadata, holders, liquidity, and risk score.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from pydantic.config import ConfigDict

router = APIRouter(prefix="/token", tags=["token"])


class TokenSummary(BaseModel):
    """Basic token metadata."""

    model_config = ConfigDict(str_strip_whitespace=True)

    chain: str = Field(..., min_length=1, max_length=50, pattern=r'^(ethereum|bsc|polygon|arbitrum|base|optimism|avalanche|solana)$')
    address: str = Field(..., min_length=26, max_length=44, pattern=r'^0x[a-fA-F0-9]{40}$')
    name: str | None = Field(None, min_length=1, max_length=100)
    symbol: str | None = Field(None, min_length=1, max_length=20)
    decimals: int | None = Field(None, ge=0, le=18)
    deployed_at: str | None = Field(None, min_length=1, max_length=100)
    deployer: str | None = Field(None, min_length=26, max_length=44, pattern=r'^0x[a-fA-F0-9]{40}$')


class TokenRisk(BaseModel):
    """Token risk assessment."""

    model_config = ConfigDict(str_strip_whitespace=True)

    address: str = Field(..., min_length=26, max_length=44, pattern=r'^0x[a-fA-F0-9]{40}$')
    chain: str = Field(..., min_length=1, max_length=50, pattern=r'^(ethereum|bsc|polygon|arbitrum|base|optimism|avalanche|solana)$')
    risk_score: int = Field(..., ge=0, le=100)
    risk_tier: str = Field(..., min_length=1, max_length=20)
    factors: list[str] = []


@router.get("/{address}", response_model=TokenSummary)
async def get_token(
    address: str,
    chain: str = Query("ethereum"),
) -> TokenSummary:
    """Fetch basic token metadata."""
    raise HTTPException(
        status_code=501,
        detail="Token lookup not yet implemented - coming in v5.1",
    )


@router.get("/{address}/risk", response_model=TokenRisk)
async def get_token_risk(address: str, chain: str = "ethereum") -> TokenRisk:
    """Compute the risk score for a token (uses Bayesian reputation + on-chain checks)."""
    raise HTTPException(
        status_code=501,
        detail="Token risk uses T01 Bayesian + T02 scanner pipeline - pending wiring",
    )


@router.get("/{address}/holders")
async def get_token_holders(address: str, chain: str = "ethereum", top: int = 50) -> dict[str, Any]:
    """Return top holders distribution for a token."""
    raise HTTPException(
        status_code=501,
        detail="Holder distribution not yet implemented - uses Postgres + Neo4j",
    )
