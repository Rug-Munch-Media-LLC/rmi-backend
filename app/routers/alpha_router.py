"""
Alpha Router
=============
Premium alpha endpoints — prediction-market insider detection and
smart-money signals. Content people pay for.

Endpoints:
  GET /api/v1/alpha/prediction-market-insiders — wallets with consistent
  winning positions on prediction markets (win rate > 60%, > 5 positions),
  with win rate, positions, recent bets, profit, and smart-money label.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.logging import get_logger
from app.domains.prediction.insider_detection import (
    MIN_POSITIONS,
    MIN_WIN_RATE,
    get_prediction_market_insiders,
)

logger = get_logger("alpha.router")

router = APIRouter(prefix="/api/v1/alpha", tags=["alpha"])


@router.get("/prediction-market-insiders")
async def prediction_market_insiders(
    min_win_rate: float = Query(default=MIN_WIN_RATE, ge=0.0, le=1.0),
    min_positions: int = Query(default=MIN_POSITIONS, ge=1, le=100),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Prediction-market insider leaderboard.

    Wallets with consistently winning resolved positions (binomial p-value
    vs a 50/50 null + timing analysis). Smart-money label requires
    win_rate > min_win_rate and >= min_positions resolved positions.
    """
    try:
        result = await get_prediction_market_insiders(min_win_rate=min_win_rate, min_positions=min_positions)
    except Exception as exc:  # fail-open — never 500 the leaderboard
        logger.warning("prediction_market_insiders failed: %s", exc)
        result = {
            "total_wallets_analyzed": 0,
            "smart_money_count": 0,
            "thresholds": {"min_win_rate": min_win_rate, "min_positions": min_positions},
            "insiders": [],
            "source": "polymarket",
        }
    insiders = result["insiders"][:limit]
    return {
        "data": insiders,
        "total_wallets_analyzed": result["total_wallets_analyzed"],
        "smart_money_count": result["smart_money_count"],
        "thresholds": result["thresholds"],
        "source": result["source"],
        "count": len(insiders),
    }
