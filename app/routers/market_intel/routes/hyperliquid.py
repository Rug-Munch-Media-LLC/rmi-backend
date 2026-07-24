"""
Market Intelligence - Hyperliquid Routes
Hyperliquid perp markets, funding rates, and action data.
"""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.routers.market_intel.providers import _hyperliquid_action, _hyperliquid_funding, _hyperliquid_markets
from app.routers.market_intel.utils import _now

router = APIRouter(tags=["market-intel"])

@router.get("/hyperliquid")
async def get_hyperliquid_data(
    request: Request,
    limit: int = Query(10, ge=1, le=50),
) -> dict:
    """Hyperliquid perp markets and funding rates."""
    now = _now()
    markets = await _hyperliquid_markets(limit=limit)
    funding = await _hyperliquid_funding(limit=limit)
    return {
        "markets": markets,
        "funding": funding,
        "total_markets": len(markets),
        "updated_at": now,
    }

@router.get("/hyperliquid-action")
async def get_hyperliquid_action(
    request: Request,
    limit: int = Query(5, ge=1, le=20),
) -> dict:
    """
    Hyperliquid 'Gain/Loss Porn': Biggest 24h movers and funding squeezes.
    Shows where the market is ripping, dumping, and who is getting liquidated/squeezed.
    """
    now = _now()
    action_data = await _hyperliquid_action(limit=limit)
    return {**action_data, "updated_at": now}
