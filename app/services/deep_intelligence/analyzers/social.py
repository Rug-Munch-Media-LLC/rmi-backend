"""
from __future__ import annotations

Social-Onchain Correlation
===========================
Map hype cycle: wallet activity spikes correlated with social mentions.
Influencer wallet tracking. Coordinated pump timing.
"""

from typing import Any

from app.services.deep_intelligence.core import client


async def social_onchain_correlation(token_address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Map hype cycle: wallet activity spikes correlated with social mentions.
    Influencer wallet tracking. Coordinated pump timing.
    """
    result = {
        "token": token_address,
        "chain": chain,
        "social_signals": {},
        "onchain_activity": {},
        "correlation": {},
        "flags": [],
        "verdict": "UNKNOWN",
    }

    dex_info = await client.dexscreener(chain, token_address)
    if "error" in dex_info:
        result["flags"].append("Could not fetch token data")
        return result

    volume_24h = float(dex_info.get("volume", {}).get("h24", 0) or 0)
    volume_6h = float(dex_info.get("volume", {}).get("h6", 0) or 0)
    price_change_24h = float(dex_info.get("priceChange", {}).get("h24", 0) or 0)
    price_change_6h = float(dex_info.get("priceChange", {}).get("h6", 0) or 0)
    mcap = float(dex_info.get("marketCap", 0) or 0)
    liquidity = float(dex_info.get("liquidity", {}).get("usd", 0) or 0)

    info = dex_info.get("info", {}) or {}
    socials = info.get("socials", []) if isinstance(info, dict) else []

    social_data = {
        "has_website": bool(info.get("websites"))
        if isinstance(info, dict) and info.get("websites")
        else False,
        "social_count": len(socials),
        "socials": socials,
        "description": info.get("description", "") if isinstance(info, dict) else "",
    }

    flags = []
    correlation_score = 0

    if price_change_24h > 100 and volume_24h > mcap * 0.5:
        correlation_score += 20
        flags.append(
            "Massive price spike with volume exceeding 50% of market cap - possible coordinated pump"
        )

    if price_change_6h > 50 and volume_6h > volume_24h * 0.5:
        correlation_score += 15
        flags.append("Recent 6h volume spike suggests coordinated buying")

    if mcap > 0 and volume_24h / max(mcap, 1) > 2:
        correlation_score += 10
        flags.append("Volume-to-mcap ratio exceeds 2x - extreme activity for size")

    if social_data["social_count"] == 0:
        correlation_score += 15
        flags.append("No social links found - possible anonymous/scam project")

    result["social_signals"] = social_data
    result["onchain_activity"] = {
        "volume_24h_usd": volume_24h,
        "volume_6h_usd": volume_6h,
        "price_change_24h_pct": price_change_24h,
        "price_change_6h_pct": price_change_6h,
        "market_cap_usd": mcap,
        "liquidity_usd": liquidity,
        "volume_to_mcap_ratio": round(volume_24h / max(mcap, 1), 2),
    }
    result["correlation"] = {
        "correlation_score": correlation_score,
        "pump_likelihood": "HIGH"
        if correlation_score >= 40
        else "MEDIUM"
        if correlation_score >= 20
        else "LOW",
    }
    result["flags"] = flags
    result["verdict"] = (
        "COORDINATED_PUMP"
        if correlation_score >= 40
        else "SUSPICIOUS_ACTIVITY"
        if correlation_score >= 20
        else "NORMAL"
    )

    return result
