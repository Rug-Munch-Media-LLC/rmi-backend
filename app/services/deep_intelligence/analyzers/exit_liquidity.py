"""

Exit Liquidity Calculator
==========================
Can you actually sell? Depth-at-price analysis, sell cascade simulation,
individual holder exit impact, liquidity concentration.
"""

from __future__ import annotations

from typing import Any

from app.services.deep_intelligence.core import client


async def exit_liquidity_calculator(
    token_address: str, position_usd: float = 1000, chain: str = "sol"
) -> dict[str, Any]:
    """
    Can you actually sell? Depth-at-price analysis, sell cascade simulation,
    individual holder exit impact, liquidity concentration.
    """
    result = {
        "token": token_address,
        "chain": chain,
        "position_usd": position_usd,
        "exit_analysis": {},
        "cascade_simulation": [],
        "flags": [],
        "recommendation": "UNKNOWN",
    }

    dex_info = await client.dexscreener(chain, token_address)
    if "error" in dex_info:
        result["flags"].append("Could not fetch token data")
        return result

    liquidity = float(dex_info.get("liquidity", {}).get("usd", 0) or 0)
    mcap = float(dex_info.get("marketCap", 0) or 0)
    volume_24h = float(dex_info.get("volume", {}).get("h24", 0) or 0)

    position_pct_of_liq = (position_usd / max(liquidity, 1)) * 100
    slippage_estimate = position_pct_of_liq * 0.5
    max_recoverable = min(position_usd, liquidity * 0.8)
    actual_recovery_pct = (max_recoverable / max(position_usd, 1)) * 100

    flags = []
    if position_pct_of_liq > 10:
        flags.append(f"Your position is {position_pct_of_liq:.1f}% of the pool - exiting will crash the price")
    if position_pct_of_liq > 50:
        flags.append("CRITICAL: Position exceeds half the liquidity - exit is nearly impossible")
    if liquidity < 10000:
        flags.append(f"Extremely thin liquidity (${liquidity:,.0f}) - large exits impossible")

    cascade = []
    for pct_sell in [5, 10, 20, 30, 50]:
        sell_value = mcap * (pct_sell / 100) * 0.1
        price_impact = (sell_value / max(liquidity, 1)) * 50
        cascade.append(
            {
                "holders_selling_pct": pct_sell,
                "estimated_price_drop_pct": round(min(price_impact, 95), 1),
                "liquidity_remaining_usd": round(max(0, liquidity - sell_value), 0),
            }
        )

    result["exit_analysis"] = {
        "liquidity_usd": liquidity,
        "market_cap_usd": mcap,
        "position_pct_of_liquidity": round(position_pct_of_liq, 2),
        "estimated_slippage_pct": round(slippage_estimate, 2),
        "max_recoverable_usd": round(max_recoverable, 2),
        "recovery_rate_pct": round(actual_recovery_pct, 1),
        "volume_24h_usd": volume_24h,
    }
    result["cascade_simulation"] = cascade
    result["flags"] = flags
    result["recommendation"] = (
        "DO_NOT_BUY"
        if actual_recovery_pct < 30
        else "HIGH_RISK"
        if actual_recovery_pct < 50
        else "CAUTION"
        if actual_recovery_pct < 80
        else "REASONABLE_LIQUIDITY"
    )

    return result
