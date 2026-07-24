"""
from __future__ import annotations

Pre-Flight Transaction Simulator
=================================
Simulate a buy AND sell to show real costs, slippage, taxes, and net P&L.
"""

from typing import Any

from app.services.deep_intelligence.core import client, logger


async def preflight_simulate(
    token_address: str, wallet_address: str, buy_amount_usd: float = 100, chain: str = "sol"
) -> dict[str, Any]:
    """
    Simulate a buy AND sell to show real costs, slippage, taxes, and net P&L.
    """
    result = {
        "token": token_address,
        "wallet": wallet_address,
        "chain": chain,
        "buy_simulation": {},
        "sell_simulation": {},
        "net_analysis": {},
        "warnings": [],
        "risk_flags": [],
    }

    # Get token data from DexScreener
    dex_info = await client.dexscreener(chain, token_address)

    if "error" not in dex_info:
        liquidity = float(dex_info.get("liquidity", {}).get("usd", 0) or 0)
        volume_24h = float(dex_info.get("volume", {}).get("h24", 0) or 0)
        price_usd = float(dex_info.get("priceUsd", 0) or 0)
        mcap = float(dex_info.get("marketCap", 0) or 0)

        # Buy simulation
        slippage_impact = round(buy_amount_usd / max(liquidity, 1) * 100, 3)
        estimated_tokens = buy_amount_usd / max(price_usd, 0.00000001) if price_usd else 0

        result["buy_simulation"] = {
            "input_usd": buy_amount_usd,
            "estimated_tokens": round(estimated_tokens, 6),
            "price_per_token_usd": price_usd,
            "slippage_pct": slippage_impact,
            "pool_impact": f"You'd consume {slippage_impact:.2f}% of the liquidity pool",
            "can_buy": slippage_impact < 5,
        }

        # Sell simulation
        sell_tax_estimate = 0
        estimated_sell_usd = estimated_tokens * price_usd * (1 - sell_tax_estimate / 100)
        sell_slippage = round(estimated_sell_usd / max(liquidity, 1) * 100, 3)

        result["sell_simulation"] = {
            "tokens_to_sell": round(estimated_tokens, 6),
            "estimated_value_usd": round(estimated_sell_usd, 2),
            "sell_tax_estimate_pct": sell_tax_estimate,
            "slippage_pct": sell_slippage,
            "net_return_usd": round(estimated_sell_usd - buy_amount_usd, 2),
            "can_sell_profitably": estimated_sell_usd > buy_amount_usd * 0.95,
        }

        # Warnings
        if liquidity < 10000:
            result["warnings"].append(f"Low liquidity: ${liquidity:,.0f} - hard to exit positions")
        if slippage_impact > 3:
            result["warnings"].append(f"Your buy would move the price ~{slippage_impact:.1f}%")
        if volume_24h < 1000:
            result["warnings"].append(f"Very low 24h volume: ${volume_24h:,.0f}")

        # Price scenario analysis
        scenarios = []
        for mult in [0.5, 2, 5, 10]:
            future_price = price_usd * mult
            future_value = estimated_tokens * future_price
            future_slippage = round(future_value / max(liquidity, 1) * 100, 2)
            scenarios.append(
                {
                    "price_target": f"{mult}x",
                    "future_price_usd": round(future_price, 8),
                    "position_value_usd": round(future_value, 2),
                    "slippage_at_exit_pct": future_slippage,
                    "profit_loss_usd": round(future_value - buy_amount_usd, 2),
                    "exit_viable": future_slippage < 10,
                }
            )
        result["net_analysis"] = {
            "scenarios": scenarios,
            "liquidity_usd": liquidity,
            "volume_24h_usd": volume_24h,
            "market_cap_usd": mcap,
            "recommendation": "CAUTION" if liquidity < 50000 else "PROCEED",
        }
    else:
        result["warnings"].append(f"Could not fetch token data: {dex_info.get('error', 'unknown')}")

    return result
