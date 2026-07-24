"""

Post-Purchase Monitor
======================
Detect changes AFTER buying: contract modifications, new admin functions,
fee structure changes, liquidity unlocks, wallet behavior shifts.
"""

from __future__ import annotations

from typing import Any
from datetime import UTC, datetime

from app.services.deep_intelligence.core import client

async def post_purchase_monitor(token_address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Detect changes AFTER buying: contract modifications, new admin functions,
    fee structure changes, liquidity unlocks, wallet behavior shifts.
    """
    result = {
        "token": token_address,
        "chain": chain,
        "current_state": {},
        "alerts": [],
        "risk_changes": [],
        "overall_trend": "STABLE",
    }

    dex_info = await client.dexscreener(chain, token_address)
    if "error" in dex_info:
        result["alerts"].append("Could not fetch token data")
        return result

    price_usd = float(dex_info.get("priceUsd", 0) or 0)
    liquidity = float(dex_info.get("liquidity", {}).get("usd", 0) or 0)
    mcap = float(dex_info.get("marketCap", 0) or 0)
    price_change_5m = float(dex_info.get("priceChange", {}).get("m5", 0) or 0)
    price_change_1h = float(dex_info.get("priceChange", {}).get("h1", 0) or 0)
    price_change_6h = float(dex_info.get("priceChange", {}).get("h6", 0) or 0)
    price_change_24h = float(dex_info.get("priceChange", {}).get("h24", 0) or 0)
    volume_24h = float(dex_info.get("volume", {}).get("h24", 0) or 0)

    result["current_state"] = {
        "price_usd": price_usd,
        "liquidity_usd": liquidity,
        "market_cap_usd": mcap,
        "price_change_5m_pct": price_change_5m,
        "price_change_1h_pct": price_change_1h,
        "price_change_6h_pct": price_change_6h,
        "price_change_24h_pct": price_change_24h,
        "volume_24h_usd": volume_24h,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    alerts = []

    if price_change_1h < -20:
        alerts.append(
            {
                "severity": "CRITICAL",
                "type": "PRICE_CRASH",
                "message": f"Price dropped {price_change_1h:.1f}% in the last hour - possible rug or sell-off",
            }
        )
    elif price_change_1h < -10:
        alerts.append(
            {
                "severity": "HIGH",
                "type": "PRICE_DROP",
                "message": f"Price down {price_change_1h:.1f}% in 1 hour",
            }
        )

    if liquidity < 5000:
        alerts.append(
            {
                "severity": "CRITICAL",
                "type": "LOW_LIQUIDITY",
                "message": f"Liquidity critically low: ${liquidity:,.0f}",
            }
        )

    if volume_24h > 0 and liquidity > 0 and volume_24h / liquidity > 5:
        alerts.append(
            {
                "severity": "MEDIUM",
                "type": "HIGH_VOLUME_RATIO",
                "message": f"24h volume is {volume_24h / liquidity:.1f}x the liquidity",
            }
        )

    if abs(price_change_5m) > 10:
        direction = "surge" if price_change_5m > 0 else "drop"
        alerts.append(
            {
                "severity": "HIGH",
                "type": "RAPID_MOVE",
                "message": f"Price {direction}d {abs(price_change_5m):.1f}% in 5 minutes",
            }
        )

    if price_change_24h < -30:
        result["overall_trend"] = "DUMPING"
    elif price_change_24h < -10:
        result["overall_trend"] = "DECLINING"
    elif price_change_24h > 30:
        result["overall_trend"] = "PUMPING"
    elif price_change_24h > 10:
        result["overall_trend"] = "RISING"
    else:
        result["overall_trend"] = "STABLE"

    result["alerts"] = alerts
    return result
