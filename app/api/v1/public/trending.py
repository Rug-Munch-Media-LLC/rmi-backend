"""GET /api/v1/trending — what is actually trading right now.

Aggregates our own indexed DEX events (plus pump.fun launch metadata and the
entity label set) into a ranked, null-honest list. ``window`` accepts only
1 / 4 / 24 / 168 hours; ``limit`` is capped at 100.

Fail-open: a dead source degrades the list and is named in ``degraded`` — the
endpoint does not 5xx because one upstream is unavailable.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.trending.schemas import TrendingResponse
from app.trending.service import build_trending

router = APIRouter(prefix="/trending", tags=["trending"])

WINDOWS = (1, 4, 24, 168)

SUPPORTED_CHAINS = (
    "ethereum",
    "bsc",
    "polygon",
    "arbitrum",
    "base",
    "optimism",
    "avalanche",
    "solana",
    "gnosis",
    "fantom",
    "scroll",
    "blast",
    "fraxtal",
    "zksync",
)


@router.get("", response_model=TrendingResponse, summary="Trending tokens")
async def get_trending(
    chain: Annotated[
        str | None,
        Query(description="Restrict to one chain; omit for all chains"),
    ] = None,
    window: Annotated[
        int,
        Query(description="Lookback window in hours: 1, 4, 24 or 168"),
    ] = 24,
    limit: Annotated[int, Query(ge=1, le=100, description="Max items returned")] = 25,
) -> TrendingResponse:
    """Return ranked trending tokens for the window."""
    if window not in WINDOWS:
        raise HTTPException(status_code=422, detail=f"window must be one of {list(WINDOWS)}")
    normalized = (chain or "").strip().lower()
    if normalized and normalized not in SUPPORTED_CHAINS:
        normalized = "__unknown__"  # yields an empty, cacheable result
    payload = await build_trending(normalized, int(window), int(limit))
    return TrendingResponse.model_validate(payload)
