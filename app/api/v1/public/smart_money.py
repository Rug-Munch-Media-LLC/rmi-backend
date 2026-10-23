"""Public smart-money endpoints - /api/v1/smart-money/*.

Surfaces the highest-conviction on-chain moves (accumulation / distribution)
detected from ``wallet_memory`` ClickHouse evidence. Every field is derived
from stored transfers + prices; anything unknown is ``null`` with a reason.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from app.domains.smart_money.models import WINDOWS, SmartMoneyMovesResponse
from app.domains.smart_money.service import DEFAULT_CHAINS, get_moves

router = APIRouter(prefix="/smart-money", tags=["smart-money"])

logger = logging.getLogger(__name__)

CHAIN_PATTERN = "^(" + "|".join(DEFAULT_CHAINS) + ")$"


def moves_provider() -> Callable[..., Any]:
    """DI seam: tests override this instead of patching the module."""
    return get_moves


@router.get("/moves", response_model=SmartMoneyMovesResponse)
def list_moves(
    chain: str | None = Query(
        None, pattern=CHAIN_PATTERN, description="Restrict to one chain"
    ),
    window: int = Query(
        24, description="Lookback window in hours: 1, 4, 24 or 168"
    ),
    limit: int = Query(25, ge=1, le=100, description="Max moves returned"),
    provider: Callable[..., Any] = Depends(moves_provider),
) -> SmartMoneyMovesResponse:
    """Return scored smart-money moves for the requested window.

    Runs synchronously (ClickHouse driver is blocking) so FastAPI dispatches
    it to the threadpool instead of stalling the event loop.
    """
    if window not in WINDOWS:
        raise HTTPException(status_code=422, detail=f"window must be one of {list(WINDOWS)}")
    try:
        items = provider(chain=chain, window=window, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("smart-money moves failed")
        raise HTTPException(
            status_code=503, detail="smart-money evidence store unavailable"
        ) from exc
    return SmartMoneyMovesResponse(items=items, generated_at=datetime.now(UTC))
