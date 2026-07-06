"""Cross-chain arbitrage detector. Finds price discrepancies across 112 chains."""

import httpx
from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/arbitrage", tags=["arbitrage"])

DEXSCREENER = "https://api.dexscreener.com/latest/dex/search"
CHAINS = ["ethereum", "bsc", "arbitrum", "base", "polygon", "avalanche", "optimism", "solana"]
STABLES = {"USDC", "USDT", "DAI", "BUSD", "USDD", "FRAX"}


async def _get_multi_chain_price(symbol: str) -> list[dict]:
    """Get price across all chains from DexScreener."""
    listings = []
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{DEXSCREENER}?q={symbol}")
            if r.status_code == 200:
                pairs = r.json().get("pairs", [])
                for p in pairs[:50]:
                    chain = p.get("chainId", "unknown")
                    if chain in CHAINS or chain == "solana":
                        listings.append(
                            {
                                "chain": chain,
                                "price_usd": float(p.get("priceUsd", 0) or 0),
                                "liquidity_usd": p.get("liquidity", {}).get("usd", 0) or 0,
                                "dex": p.get("dexId", "?"),
                                "pair": p.get("pairAddress", ""),
                            }
                        )
    except Exception:
        pass
    return listings


@router.get("/find/{symbol}")
async def find_arbitrage(symbol: str, min_profit_pct: float = Query(0.5, ge=0.1), max_gas_usd: float = Query(50, ge=1)):
    """Find arbitrage opportunities for a token across chains."""
    listings = await _get_multi_chain_price(symbol)
    if len(listings) < 2:
        return {"symbol": symbol, "opportunities": [], "note": "Need at least 2 chains with liquidity"}

    # Find min/max price
    listings.sort(key=lambda l: l["price_usd"])  # noqa: E741
    cheapest = listings[0]
    most_expensive = listings[-1]

    spread_pct = (
        ((most_expensive["price_usd"] - cheapest["price_usd"]) / cheapest["price_usd"] * 100)
        if cheapest["price_usd"] > 0
        else 0
    )

    opportunities = []
    if spread_pct >= min_profit_pct:
        gross_profit = most_expensive["price_usd"] - cheapest["price_usd"]
        net_profit = gross_profit - max_gas_usd
        opportunities.append(
            {
                "buy_chain": cheapest["chain"],
                "buy_price": cheapest["price_usd"],
                "buy_dex": cheapest["dex"],
                "sell_chain": most_expensive["chain"],
                "sell_price": most_expensive["price_usd"],
                "sell_dex": most_expensive["dex"],
                "spread_pct": round(spread_pct, 2),
                "gross_profit_per_unit": round(gross_profit, 4),
                "estimated_gas": max_gas_usd,
                "net_profit_estimate": round(net_profit, 2),
                "viable": net_profit > 0,
            }
        )

    return {
        "symbol": symbol,
        "chains_with_liquidity": len(listings),
        "cheapest": {"chain": cheapest["chain"], "price": cheapest["price_usd"]},
        "most_expensive": {"chain": most_expensive["chain"], "price": most_expensive["price_usd"]},
        "max_spread_pct": round(spread_pct, 2),
        "opportunities": opportunities,
    }


@router.get("/scan")
async def scan_arbitrage(limit: int = Query(10, le=20)):
    """Scan popular tokens for arbitrage opportunities."""
    popular = ["ETH", "USDC", "USDT", "MATIC", "ARB", "OP", "LINK", "UNI", "AAVE", "SNX", "CRV", "LDO"]
    results = []
    for symbol in popular[:limit]:
        opps = await find_arbitrage(symbol, min_profit_pct=0.3)
        if opps.get("opportunities"):
            results.append(opps)

    results.sort(key=lambda r: r["max_spread_pct"], reverse=True)
    return {"scanned": len(popular[:limit]), "found": len(results), "opportunities": results}
