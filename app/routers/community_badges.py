"""
Community Badges Router - FastAPI endpoints for voting and badge queries.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.community_badges import (
    cast_vote,
    get_leaderboard,
    get_token_votes,
    get_user_badge,
    resolve_token_outcome,
)

router = APIRouter(prefix="/api/v1/community", tags=["community"])


# ── Request models ────────────────────────────────────────────────


class VoteRequest(BaseModel):
    token_address: str = Field(..., min_length=32, max_length=128)
    chain: str = Field(default="solana")
    voter_id: str = Field(..., min_length=1, max_length=256)
    vote: str = Field(..., pattern=r"^(clean|malicious)$")


class ResolveRequest(BaseModel):
    token_address: str = Field(..., min_length=32, max_length=128)
    chain: str = Field(default="solana")
    outcome: str = Field(..., pattern=r"^(clean|malicious|rug_confirmed)$")


# ── Endpoints ─────────────────────────────────────────────────────


@router.post("/vote")
async def api_cast_vote(req: VoteRequest):
    """Cast a vote on whether a token is clean or malicious."""
    result = cast_vote(
        token_address=req.token_address,
        chain=req.chain,
        voter_id=req.voter_id,
        vote=req.vote,
    )
    if result.get("success") is False:
        raise HTTPException(status_code=400, detail=result.get("error", "Vote failed"))
    return result


@router.post("/resolve")
async def api_resolve_outcome(req: ResolveRequest):
    """Resolve a token's outcome (admin/cron). Updates all voter badges."""
    result = resolve_token_outcome(
        token_address=req.token_address,
        chain=req.chain,
        outcome=req.outcome,
    )
    if result.get("success") is False:
        raise HTTPException(status_code=400, detail=result.get("error", "Resolve failed"))
    return result


@router.get("/badge/{user_id}")
async def api_get_badge(user_id: str):
    """Get a user's badge profile."""
    badge = get_user_badge(user_id)
    if badge is None:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "user_id": badge.user_id,
        "total_votes": badge.total_votes,
        "correct_votes": badge.correct_votes,
        "current_tier": badge.current_tier,
        "next_tier": badge.next_tier,
        "votes_needed_for_next": badge.votes_needed_for_next,
        "badges_earned": badge.badges_earned,
        "accuracy_pct": badge.accuracy_pct,
    }


@router.get("/votes/{chain}/{token_address:path}")
async def api_get_token_votes(token_address: str, chain: str):
    """Get all votes for a token."""
    result = get_token_votes(token_address, chain)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/leaderboard")
async def api_get_leaderboard(limit: int = Query(default=20, le=100)):
    """Get community leaderboard (top sleuths)."""
    return {"leaderboard": get_leaderboard(limit)}
