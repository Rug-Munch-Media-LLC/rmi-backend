"""GMGN intelligence endpoints — our normalization and our ranking.

Every response carries ``missing`` (null + reason) and ``sources`` (which
GMGN endpoint supplied a fact), so a thin payload explains itself. Nothing
here echoes GMGN's rank order: lists are sorted by our score.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.logging import get_logger
from app.domains.gmgn_intel import get_gmgn_intel_service

log = get_logger(__name__)
router = APIRouter()


@router.get("/descriptor")
async def descriptor() -> dict[str, Any]:
    """Stages, score weights and endpoints this layer uses (contract)."""
    return get_gmgn_intel_service().descriptor()


@router.get("/lifecycle")
async def lifecycle(
    chain: str = Query("solana", description="solana | ethereum | bsc | base"),
    limit: int = Query(60, ge=1, le=80, description="rows per trenches bucket"),
) -> dict[str, Any]:
    """Token lifecycle: new / bonding / almost_graduated / graduated + trending count."""
    return await get_gmgn_intel_service().lifecycle(chain, limit)


@router.get("/trending")
async def trending(
    chain: str = Query("solana"),
    interval: str = Query("1h"),
    limit: int = Query(50, ge=1, le=100),
) -> dict[str, Any]:
    """Trending tokens re-ranked by our score (volume/liquidity/holders/social/security)."""
    return await get_gmgn_intel_service().ranked_trending(chain, interval, limit)


@router.get("/token/{address}")
async def token_intel(
    address: str,
    chain: str = Query("solana"),
    smart_money: bool = Query(True, description="include holder-derived smart-money view"),
) -> dict[str, Any]:
    """Token detail: info + security + score (+ smart money)."""
    return await get_gmgn_intel_service().token_intel(address, chain, smart_money)


@router.get("/wallet/{wallet}")
async def wallet_intel(
    wallet: str,
    chain: str = Query("solana"),
    period: str = Query("7d"),
) -> dict[str, Any]:
    """Wallet stats + activity (signed holdings are not called — no private key)."""
    return await get_gmgn_intel_service().wallet_intel(wallet, chain, period)
