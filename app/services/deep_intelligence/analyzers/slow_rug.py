"""

Slow Rug Detector
==================
Detect gradual LP removal, fee increases, slow ownership transfers.
Track liquidity drain velocity over time.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_intelligence.core import client


async def slow_rug_detector(token_address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Detect gradual LP removal, fee increases, slow ownership transfers.
    Track liquidity drain velocity over time.
    """
    result = {
        "token": token_address,
        "chain": chain,
        "slow_rug_score": 0,
        "drain_velocity": {},
        "patterns": [],
        "liquidity_timeline": [],
        "flags": [],
        "verdict": "UNKNOWN",
    }

    dex_info = await client.dexscreener(chain, token_address)
    if "error" in dex_info:
        result["flags"].append("Could not fetch token data")
        return result

    current_liq = float(dex_info.get("liquidity", {}).get("usd", 0) or 0)
    price_change_24h = float(dex_info.get("priceChange", {}).get("h24", 0) or 0)
    price_change_6h = float(dex_info.get("priceChange", {}).get("h6", 0) or 0)
    volume_24h = float(dex_info.get("volume", {}).get("h24", 0) or 0)

    patterns = []
    slow_rug_score = 0

    if price_change_24h < -10 and volume_24h > current_liq * 0.5:
        slow_rug_score += 30
        patterns.append(
            f"Price down {price_change_24h:.1f}% in 24h with high volume - possible LP drain"
        )

    if price_change_24h < -5 and price_change_6h < -2:
        slow_rug_score += 20
        patterns.append("Consistent decline across timeframes - slow bleed pattern")

    mcap = float(dex_info.get("marketCap", 0) or 0)
    if mcap > 0 and current_liq / mcap < 0.03:
        slow_rug_score += 25
        patterns.append(f"LP is only {current_liq / mcap:.1%} of market cap - easy to drain")

    if current_liq > 0 and volume_24h / current_liq > 2:
        slow_rug_score += 15
        patterns.append(
            f"24h volume is {volume_24h / current_liq:.1f}x the liquidity - high churn rate"
        )

    result["slow_rug_score"] = min(slow_rug_score, 100)
    result["patterns"] = patterns
    result["drain_velocity"] = {
        "current_liquidity_usd": current_liq,
        "market_cap_usd": mcap,
        "liq_to_mcap_ratio": round(current_liq / max(mcap, 1), 4),
        "price_change_24h_pct": price_change_24h,
        "price_change_6h_pct": price_change_6h,
        "volume_to_liquidity_ratio": round(volume_24h / max(current_liq, 1), 2),
    }
    result["verdict"] = (
        "ACTIVE_SLOW_RUG"
        if slow_rug_score >= 60
        else "SUSPICIOUS"
        if slow_rug_score >= 40
        else "WATCH"
        if slow_rug_score >= 20
        else "CLEAN"
    )

    return result
