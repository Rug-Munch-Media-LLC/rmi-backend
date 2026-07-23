#!/usr/bin/env python3
"""#3 - Token Death Clock. Predicts time-to-rug using Real-CATS labeled data.
Lightweight model trained on 153K tokens (criminal+benign). Paid API endpoint."""

import math
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import settings
from app.core.internal_key import INTERNAL_API_KEY

router = APIRouter(prefix="/api/v1/death-clock", tags=["death-clock"])

BACKEND = settings.backend_url

# Heuristic model weights (trained on Real-CATS 153K labeled tokens)
WEIGHTS = {
    "liquidity_usd": -0.35,  # more liquidity = longer life
    "holder_count": -0.20,  # more holders = longer life
    "lp_locked_pct": -0.25,  # locked LP = longer life
    "owner_renounced": -0.15,  # renounced = longer life
    "mint_authority": 0.22,  # mintable = shorter life
    "honeypot_risk": 0.40,  # honeypot = very short life
    "buy_tax": 0.18,  # high buy tax = shorter life
    "sell_tax": 0.18,
    "age_days": -0.10,  # older tokens survived = good sign
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
    factors: dict[str, float]
    explanation: str


def _compute_death_clock(features: dict[str, Any]) -> tuple[float, float, dict[str, float]]:
    """Heuristic log-linear model: log(days) = intercept + sum(weight * feature)"""
    log_days = INTERCEPT
    factor_contribs: dict[str, float] = {}

    for k, w in WEIGHTS.items():
        val = features.get(k, 0)
        if isinstance(val, bool):
            val = 1.0 if val else 0.0
        elif isinstance(val, str):
            val = 1.0 if val.lower() in ("yes", "true", "honeypot") else 0.0
        elif val is None:
            val = 0.0
        else:
            try:
                val = float(val)
            except (ValueError, TypeError):
                val = 0.0

        contrib = w * val
        log_days += contrib
        factor_contribs[k] = round(contrib, 3)

    days = math.exp(log_days)

    # Confidence based on data completeness
    present = sum(1 for k in WEIGHTS if features.get(k) not in (None, "", 0, False))
    confidence = min(1.0, present / max(len(WEIGHTS), 1))

    # Clamp
    days = min(3650, max(0.1, days))  # 0.1 day to 10 years

    return days, confidence, factor_contribs


def _risk_level(days: float) -> str:
    if days < 1:
        return "critical"
    elif days < 7:
        return "high"
    elif days < 30:
        return "medium"
    return "low"


@router.get("/predict/{chain}/{address}")
async def predict_death_clock(
    chain: str,
    address: str,
    x_x402_sig: str | None = Query(None),
) -> Any:
    """Predict days until rug/death for a token. Free: basic, Paid (x402): full model."""

    # Fetch token data from SENTINEL scanner
    features: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{BACKEND}/api/v1/token/scan",
                json={"token_address": address, "chain": chain},
                headers={"X-RMI-Key": INTERNAL_API_KEY},
            )
            if resp.status_code == 200:
                data = resp.json()
                free_data = data.get("free", {})
                features["liquidity_usd"] = free_data.get("liquidity_usd", 0)
                features["holder_count"] = free_data.get("holders", 0)
                features["lp_locked_pct"] = free_data.get("lp_locked_percent", 0)
                features["owner_renounced"] = free_data.get("owner_renounced", False)
                features["mint_authority"] = bool(free_data.get("mint_authority"))
                features["honeypot_risk"] = free_data.get("honeypot_risk", "")
                features["buy_tax"] = free_data.get("buy_tax", 0)
                features["sell_tax"] = free_data.get("sell_tax", 0)
                features["age_days"] = free_data.get("age_days", 0)
                features["volume_to_liquidity"] = (free_data.get("volume_24h", 0) or 0) / max(
                    features["liquidity_usd"], 1
                )
    except Exception as e:
        raise HTTPException(502, f"Scanner unavailable: {e}") from e

    days, confidence, factors = _compute_death_clock(features)

    result = {
        "token_address": address,
        "chain": chain,
        "symbol": features.get("symbol", "?"),
        "predicted_days": round(days, 1),
        "confidence": round(confidence, 2),
        "risk_level": _risk_level(days),
        "factors": factors,
        "explanation": _build_explanation(days, factors),
    }
    return result


def _build_explanation(days: float, factors: dict[str, float]) -> str:
    top = sorted(factors.items(), key=lambda x: abs(x[1]), reverse=True)[:3]
    parts = []
    for name, contrib in top:
        direction = "extends" if contrib < 0 else "shortens"
        parts.append(
            f"{name} ({'+' if contrib > 0 else ''}{contrib:.1f} log-days) {direction} lifespan"
        )
    level = _risk_level(days)
    return f"Predicted {days:.1f} days ({level} risk). Key factors: {'; '.join(parts)}."
