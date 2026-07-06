"""
Liquidity Watchdog Router
=========================
FastAPI endpoints for monitoring liquidity lock expiries.

Endpoints:
  GET  /api/v1/alerts/liquidity-expiring  → Upcoming lock expiries within 24h
  POST /api/v1/alerts/liquidity-track     → Register a token for monitoring
"""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.liquidity_watchdog import (
    check_all_locks_summary,
    check_expiring_locks,
    get_tracked_tokens,
    track_token,
    untrack_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/alerts", tags=["liquidity-watchdog"])


# ── Request/Response models ──────────────────────────────────────


class LiquidityTrackRequest(BaseModel):
    token_address: str = Field(..., description="Token contract/mint address")
    chain: str = Field(..., description="Blockchain name (e.g. solana, ethereum, bsc)")
    lock_expiry_timestamp: int = Field(..., description="Unix timestamp (seconds) when lock expires")
    liquidity_amount: float = Field(0.0, description="Locked liquidity amount in USD")


class LiquidityTrackResponse(BaseModel):
    status: str = "ok"
    message: str = "Token registered for liquidity expiry monitoring"
    data: dict[str, Any]


class ExpiringLock(BaseModel):
    token: str
    chain: str
    lock_expiry: int
    hours_remaining: float
    liquidity_amount: float


class LiquidityExpiringResponse(BaseModel):
    status: str = "ok"
    count: int = 0
    expiring: list[ExpiringLock] = []


class LiquiditySummaryResponse(BaseModel):
    status: str = "ok"
    total_tracked: int = 0
    already_expired: int = 0
    expiring_soon: int = 0
    safe: int = 0
    expiring_tokens: list[ExpiringLock] = []


# ── Endpoints ────────────────────────────────────────────────────


@router.post("/liquidity-track", response_model=LiquidityTrackResponse)
async def post_liquidity_track(req: LiquidityTrackRequest):
    """Register a token for liquidity lock expiry monitoring.

    This will be called by a cron job that pings Telegram/community channels
    when locks are about to expire.

    The token's lock expiry is checked against the current time to determine
    if it's already expired (returns a warning but still registers it).
    """
    if not req.token_address or not req.token_address.strip():
        raise HTTPException(status_code=400, detail="token_address is required")
    if not req.chain or not req.chain.strip():
        raise HTTPException(status_code=400, detail="chain is required")
    if req.lock_expiry_timestamp <= 0:
        raise HTTPException(
            status_code=400,
            detail="lock_expiry_timestamp must be a positive unix timestamp",
        )

    data = track_token(
        token_address=req.token_address,
        chain=req.chain,
        lock_expiry_timestamp=req.lock_expiry_timestamp,
        liquidity_amount=req.liquidity_amount or 0.0,
    )

    return LiquidityTrackResponse(
        status="ok",
        message="Token registered for liquidity expiry monitoring",
        data=data,
    )


@router.get("/liquidity-expiring", response_model=LiquidityExpiringResponse)
async def get_liquidity_expiring(
    hours: float | None = Query(
        None,
        description="Override the expiry window in hours (default: 24). "
        "Only returns locks expiring within this many hours.",
    ),
):
    """Get all tracked tokens with liquidity locks expiring within the default
    24-hour window (or a custom window via the `hours` query parameter).

    Returns tokens sorted by urgency (most imminent first).
    """
    if hours is not None and hours <= 0:
        raise HTTPException(status_code=400, detail="hours must be > 0")

    # If a custom hours window is given, we temporarily override the module's
    # default and re-check. Since check_expiring_locks() uses a module constant,
    # we just call it once and filter further if needed.
    expiring = check_expiring_locks()

    if hours is not None:
        expiring = [e for e in expiring if e["hours_remaining"] <= hours]

    expiring_models = [ExpiringLock(**e) for e in expiring]

    return LiquidityExpiringResponse(
        status="ok",
        count=len(expiring_models),
        expiring=expiring_models,
    )


@router.get("/liquidity-tracked", response_model=dict)
async def get_liquidity_tracked():
    """List all tokens currently being monitored for liquidity expiry."""
    tokens = get_tracked_tokens()
    return {
        "status": "ok",
        "count": len(tokens),
        "tokens": tokens,
    }


@router.get("/liquidity-summary", response_model=LiquiditySummaryResponse)
async def get_liquidity_summary():
    """Get a summary of all tracked liquidity locks with counts."""
    summary = check_all_locks_summary()
    return LiquiditySummaryResponse(
        status="ok",
        total_tracked=summary["total_tracked"],
        already_expired=summary["already_expired"],
        expiring_soon=summary["expiring_soon"],
        safe=summary["safe"],
        expiring_tokens=[ExpiringLock(**e) for e in summary["expiring_tokens"]],
    )


@router.delete("/liquidity-track/{chain}/{token_address}", response_model=dict)
async def delete_liquidity_track(chain: str, token_address: str):
    """Remove a token from liquidity expiry monitoring."""
    removed = untrack_token(token_address, chain)
    if not removed:
        raise HTTPException(
            status_code=404,
            detail=f"Token {token_address} on {chain} is not being tracked",
        )
    return {
        "status": "ok",
        "message": f"Token {token_address} on {chain} removed from monitoring",
    }
