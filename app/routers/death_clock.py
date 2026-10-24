#!/usr/bin/env python3
"""#3 - Token Death Clock. Predicts time-to-rug using Real-CATS labeled data.

Trained on 153K tokens (criminal+benign). Response contract (code-not-methods):
the caller gets the prediction, the risk band and human-readable reasons.
Weights, intercept and per-factor numeric contributions stay server-side.

``factors`` is DEPRECATED: it keeps its key for one release, returning a
direction word per feature, and is removed in ``DEPRECATION_RELEASE``.
"""

from __future__ import annotations

import math
from typing import Any

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings
from app.core.errors import UpstreamError
from app.core.internal_key import INTERNAL_API_KEY
from app.core.logging import get_logger
from app.routers.death_clock_reasons import as_number, reasons_for

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/death-clock", tags=["death-clock"])
BACKEND = settings.backend_url
MODEL_VERSION = "heuristic-2026-10"
DEPRECATION_RELEASE = "0.4.0"
_deprecation_logged = False

# Internal model parameters — NEVER serialized into a response.
WEIGHTS = {
    "liquidity_usd": -0.35,
    "holder_count": -0.20,
    "lp_locked_pct": -0.25,
    "owner_renounced": -0.15,
    "mint_authority": 0.22,
    "honeypot_risk": 0.40,
    "buy_tax": 0.18,
    "sell_tax": 0.18,
    "age_days": -0.10,
    "volume_to_liquidity": -0.12,
}
INTERCEPT = 4.2  # baseline ~4.2 log-days


class DeathClockResult(BaseModel):
    token_address: str
    chain: str
    symbol: str
    predicted_days: float
    confidence: float  # 0-1
    risk_level: str  # low | medium | high | critical
    reasons: list[str]
    factors: dict[str, str] = {}  # DEPRECATED direction map
    model_version: str = MODEL_VERSION
    explanation: str


def _score(features: dict[str, Any]) -> tuple[float, float, dict[str, str]]:
    """Log-linear model. Returns (days, confidence, per-feature direction)."""
    log_days = INTERCEPT
    directions: dict[str, str] = {}
    for name, weight in WEIGHTS.items():
        contrib = weight * as_number(features.get(name))
        log_days += contrib
        directions[name] = "shortens" if contrib > 0 else "extends" if contrib < 0 else "neutral"
    days = min(3650, max(0.1, math.exp(log_days)))
    present = sum(1 for name in WEIGHTS if features.get(name) not in (None, "", 0, False))
    return days, min(1.0, present / max(len(WEIGHTS), 1)), directions


def _risk_level(days: float) -> str:
    if days < 1:
        return "critical"
    if days < 7:
        return "high"
    if days < 30:
        return "medium"
    return "low"


def _payload(address: str, chain: str, features: dict[str, Any]) -> dict[str, Any]:
    """Build the reduced response. Pure — no network, safe to unit test."""
    global _deprecation_logged
    if not _deprecation_logged:
        log.warning(
            "death_clock: `factors` is deprecated and carries directions only; removed in %s",
            DEPRECATION_RELEASE,
        )
        _deprecation_logged = True
    days, confidence, directions = _score(features)
    risk_level = _risk_level(days)
    reasons = reasons_for(features)
    return {
        "token_address": address,
        "chain": chain,
        "symbol": features.get("symbol", "?"),
        "predicted_days": round(days, 1),
        "confidence": round(confidence, 2),
        "risk_level": risk_level,
        "reasons": reasons,
        "factors": directions,
        "model_version": MODEL_VERSION,
        "explanation": f"Predicted {days:.1f} days ({risk_level} risk). " + "; ".join(reasons[:3]) + ".",
    }


def _features_from_scan(data: dict[str, Any]) -> dict[str, Any]:
    free = data.get("free", {})
    liquidity = free.get("liquidity_usd", 0)
    return {
        "symbol": free.get("symbol", "?"),
        "liquidity_usd": liquidity,
        "holder_count": free.get("holders", 0),
        "lp_locked_pct": free.get("lp_locked_percent", 0),
        "owner_renounced": free.get("owner_renounced", False),
        "mint_authority": bool(free.get("mint_authority")),
        "honeypot_risk": free.get("honeypot_risk", ""),
        "buy_tax": free.get("buy_tax", 0),
        "sell_tax": free.get("sell_tax", 0),
        "age_days": free.get("age_days", 0),
        "volume_to_liquidity": (free.get("volume_24h", 0) or 0) / max(liquidity, 1),
    }


@router.get("/predict/{chain}/{address}", response_model=DeathClockResult)
async def predict_death_clock(chain: str, address: str) -> dict[str, Any]:
    """Predict days until rug/death for a token.

    Free endpoint: no x402 price is attached to this route, so it no longer
    accepts an ``x_x402_sig`` header. Accepting an unverified payment signature
    advertised a bypass that never existed.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BACKEND}/api/v1/token/scan",
                json={"token_address": address, "chain": chain},
                headers={"X-RMI-Key": INTERNAL_API_KEY},
            )
            if resp.status_code != 200:
                raise UpstreamError(f"Scanner returned {resp.status_code}")
            features = _features_from_scan(resp.json())
    except UpstreamError:
        raise
    except Exception as e:
        raise UpstreamError(f"Scanner unavailable: {e}") from e
    return _payload(address, chain, features)


__all__ = ["DEPRECATION_RELEASE", "router"]
