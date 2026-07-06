"""Public token endpoints - /api/v1/token/*.

Stub for unauthenticated token queries. Real implementation will
fetch token metadata, holders, liquidity, and risk score.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/token", tags=["token"])


class TokenSummary(BaseModel):
    """Basic token metadata."""

    chain: str
    address: str
    name: str | None = None
    symbol: str | None = None
    decimals: int | None = None
    deployed_at: str | None = None
    deployer: str | None = None


class TokenRisk(BaseModel):
    """Token risk assessment."""

    address: str
    chain: str
    risk_score: int
    risk_tier: str
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
