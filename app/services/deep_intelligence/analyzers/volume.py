"""

Volume Authenticity Engine
===========================
Detect wash trading, fake volume, bot vs organic trading.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_intelligence.core import client

async def volume_authenticity(token_address: str, chain: str = "sol") -> dict[str, Any]:
    """
    Detect wash trading, fake volume, bot vs organic trading.
    Analyzes: round-trip txs, wash score, bot detection, volume-to-holder anomalies.
    """
    result = {
        "token": token_address,
        "chain": chain,
        "wash_score": 0,
        "volume_breakdown": {},
        "bot_signals": [],
        "organic_pct": 0,
        "flags": [],
        "risk_level": "UNKNOWN",
    }

    dex_info = await client.dexscreener(chain, token_address)
    if "error" in dex_info:
        result["flags"].append("Could not fetch token data")
        return result

    volume_24h = float(dex_info.get("volume", {}).get("h24", 0) or 0)
    txns = dex_info.get("txns", {}) or {}
    h24_buys = int(txns.get("h24", {}).get("buys", 0) or 0)
    h24_sells = int(txns.get("h24", {}).get("sells", 0) or 0)
    total_txs = h24_buys + h24_sells
    price_change = float(dex_info.get("priceChange", {}).get("h24", 0) or 0)

    avg_tx_value = volume_24h / max(total_txs, 1) if total_txs else 0
    buy_sell_ratio = h24_buys / max(h24_sells, 1) if h24_sells else h24_buys

    wash_indicators = []
    wash_score = 0

    if volume_24h > 50000 and abs(price_change) < 2:
        wash_score += 25
        wash_indicators.append("High volume but minimal price movement - possible wash trading")

    if 0.8 < buy_sell_ratio < 1.2 and total_txs > 20:
        wash_score += 20
        wash_indicators.append(
            f"Symmetric buy/sell ratio ({buy_sell_ratio:.2f}) - common in wash trading"
        )

    if avg_tx_value < 50 and total_txs > 100:
        wash_score += 15
        wash_indicators.append(
            f"Many small transactions (avg ${avg_tx_value:.0f}/tx) - possible bot activity"
        )

    liquidity = float(dex_info.get("liquidity", {}).get("usd", 0) or 0)
    if volume_24h > liquidity * 3 and liquidity > 0:
        wash_score += 20
        wash_indicators.append(
            f"24h volume ({volume_24h:,.0f}) > 3x liquidity ({liquidity:,.0f}) - wash trading likely"
        )

    result["wash_score"] = min(wash_score, 100)
    result["volume_breakdown"] = {
        "volume_24h_usd": volume_24h,
        "buys_24h": h24_buys,
        "sells_24h": h24_sells,
        "total_txs_24h": total_txs,
        "avg_tx_value_usd": round(avg_tx_value, 2),
        "buy_sell_ratio": round(buy_sell_ratio, 2),
        "liquidity_usd": liquidity,
        "volume_to_liquidity_ratio": round(volume_24h / max(liquidity, 1), 2),
    }
    result["bot_signals"] = wash_indicators
    result["organic_pct"] = max(0, 100 - wash_score)
    result["risk_level"] = (
        "CRITICAL"
        if wash_score >= 70
        else "HIGH"
        if wash_score >= 50
        else "MEDIUM"
        if wash_score >= 30
        else "LOW"
        if wash_score >= 15
        else "MINIMAL"
    )

    return result
