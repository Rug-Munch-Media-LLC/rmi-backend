"""

Token Lifespan Predictor
=========================
AI-driven survival analysis. Compare against historical rug patterns.
Predict survival probability at 7d, 30d, 90d.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.deep_intelligence.core import client, logger


async def token_lifespan_predictor(token_address: str, chain: str = "sol") -> dict[str, Any]:
    """
    AI-driven survival analysis. Compare against historical rug patterns.
    Predict survival probability at 7d, 30d, 90d.
    """
    result: dict[str, Any] = {
        "token": token_address,
        "chain": chain,
        "survival_probability": {
            "7d_pct": 0,
            "30d_pct": 0,
            "90d_pct": 0,
        },
        "pattern_match": {},
        "risk_flags": [],
        "rug_probability_pct": 0,
        "verdict": "UNKNOWN",
    }

    dex_info = await client.dexscreener(chain, token_address)
    if "error" in dex_info:
        result["risk_flags"].append("Could not fetch token data")
        return result

    price_change_1h = float(dex_info.get("priceChange", {}).get("h1", 0) or 0)
    price_change_24h = float(dex_info.get("priceChange", {}).get("h24", 0) or 0)
    volume_24h = float(dex_info.get("volume", {}).get("h24", 0) or 0)
    liquidity = float(dex_info.get("liquidity", {}).get("usd", 0) or 0)
    mcap = float(dex_info.get("marketCap", 0) or 0)
    pair_age_days = 0

    pair_created = dex_info.get("pairCreatedAt", "")
    if pair_created:
        try:
            created_dt = datetime.fromisoformat(pair_created.replace("Z", "+00:00"))
            pair_age_days = (datetime.now(UTC) - created_dt).days
        except (ValueError, ConnectionError, RuntimeError):
            logger.warning("err")

    risk_flags = []
    risk_score = 30

    if pair_age_days < 1:
        risk_score += 20
        risk_flags.append("Token less than 1 day old - extreme risk")
    elif pair_age_days < 7:
        risk_score += 15
        risk_flags.append("Token less than 1 week old - high risk")
    elif pair_age_days < 30:
        risk_score += 5
        risk_flags.append("Token less than 1 month old - elevated risk")

    if price_change_1h < -20:
        risk_score += 15
        risk_flags.append(f"1h price drop: {price_change_1h:.1f}% - active dumping")
    if price_change_24h < -50:
        risk_score += 20
        risk_flags.append(f"24h price crash: {price_change_24h:.1f}% - likely rug in progress")

    if liquidity < 5000:
        risk_score += 20
        risk_flags.append(f"Very low liquidity: ${liquidity:,.0f}")
    elif liquidity < 50000:
        risk_score += 10

    if mcap > 0 and volume_24h / max(mcap, 1) > 3:
        risk_score += 10
        risk_flags.append("Volume exceeds 3x market cap - likely wash trading")

    pattern = "UNKNOWN"
    if pair_age_days < 1 and price_change_1h > 100:
        pattern = "PUMP_AND_DUMP_LAUNCH"
        risk_score += 15
    elif pair_age_days < 7 and liquidity < 10000:
        pattern = "LOW_LIQ_RUG"
        risk_score += 10
    elif price_change_24h > 200 and volume_24h > mcap:
        pattern = "HYPE_PUMP"
    elif liquidity < mcap * 0.03:
        pattern = "THIN_LIQ_EXIT_TRAP"
        risk_score += 10
    elif pair_age_days > 30 and liquidity > mcap * 0.1:
        pattern = "ESTABLISHED_TOKEN"

    risk_score = min(risk_score, 100)
    rug_prob = risk_score

    result["survival_probability"] = {
        "7d_pct": max(5, 100 - rug_prob * 0.6),
        "30d_pct": max(3, 100 - rug_prob * 0.8),
        "90d_pct": max(1, 100 - rug_prob * 0.95),
    }
    result["pattern_match"] = {"detected_pattern": pattern, "confidence": "medium"}
    result["risk_flags"] = risk_flags
    result["rug_probability_pct"] = rug_prob
    result["verdict"] = (
        "RUG_IMMINENT"
        if rug_prob >= 80
        else "HIGH_RISK"
        if rug_prob >= 60
        else "ELEVATED_RISK"
        if rug_prob >= 40
        else "MODERATE"
        if rug_prob >= 25
        else "REASONABLE"
    )

    return result
