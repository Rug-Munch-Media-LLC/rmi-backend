"""Public wallet endpoints — /api/v1/wallet/*.

Stub for unauthenticated wallet queries. Real implementation will
resolve wallets, fetch labels, and return balance/history data.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/wallet", tags=["wallet"])


class WalletResolveResponse(BaseModel):
    """Response for wallet resolution."""

    chain: str
    address: str
    labels: list[str] = []
    entity: str | None = None
    balance_usd: float | None = None
    tx_count: int | None = None


@router.get("/{address}", response_model=WalletResolveResponse)
async def resolve_wallet(
    address: str,
    chain: str = Query("ethereum", description="Blockchain (ethereum, solana, base, etc.)"),
) -> WalletResolveResponse:
    """Resolve a wallet address to its labels + summary.

    Returns 501 stub until label resolution is wired up.
    """
    raise HTTPException(
        status_code=501,
        detail="Wallet resolution not yet implemented — coming in v5.1",
    )


@router.get("/{address}/labels", response_model=list[dict[str, Any]])
async def get_wallet_labels(address: str, chain: str = "ethereum") -> list[dict[str, Any]]:
    """Return labels for a wallet from all federated sources."""
    raise HTTPException(
        status_code=501,
        detail="Federated labels API pending — see T11",
    )


@router.get("/{address}/history")
async def get_wallet_history(address: str, chain: str = "ethereum") -> dict[str, Any]:
    """Return transaction history summary for a wallet."""
    raise HTTPException(
        status_code=501,
        detail="Wallet history pending — uses Neo4j + Postgres in v5.1",
    )
