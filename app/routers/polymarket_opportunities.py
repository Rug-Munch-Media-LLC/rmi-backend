"""GET /api/v1/polymarket/opportunities — ranked Polymarket opportunity feed.

Read-only market *data*: discovery via Gamma, public-wallet concentration via
Data API v2, deterministic ranking with documented weights
(``app/domains/prediction/opportunities/scoring.py``). No order placement, no
execution, no synthesized referral links — ``referral_url`` mirrors the operator
env var ``POLYMARKET_OPPORTUNITIES_REFERRAL_URL`` and is null when unset.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.logging import get_logger
from app.domains.prediction.opportunities import OpportunitiesResponse, build_opportunities

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/polymarket", tags=["polymarket"])


@router.get("/opportunities", response_model=OpportunitiesResponse)
async def list_polymarket_opportunities(
    limit: int = Query(25, ge=1, le=100, description="Max ranked markets returned"),
    window_hours: int = Query(720, ge=1, le=8760, description="Only markets resolving within this window"),
    min_volume_usd: float = Query(10_000.0, ge=0.0, description="Gamma volume_num_min"),
    min_liquidity_usd: float = Query(1_000.0, ge=0.0, description="Gamma liquidity_num_min"),
    probe_holders: int = Query(5, ge=0, le=10, description="Top-N markets to probe for public holder concentration"),
) -> OpportunitiesResponse:
    """Rank Polymarket markets by volume, liquidity, odds movement, time-to-resolution,
    catalyst keywords and public whale concentration.

    Each item carries outcomes/prices, volume, liquidity, end date, odds delta,
    public-wallet evidence with source URLs, a 0..1 score and a per-component
    breakdown whose null members explain why the input was unavailable.
    """
    return await build_opportunities(
        limit=limit,
        window_hours=window_hours,
        min_volume_usd=min_volume_usd,
        min_liquidity_usd=min_liquidity_usd,
        probe_holders=probe_holders,
    )
