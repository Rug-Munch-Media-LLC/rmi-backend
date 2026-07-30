#!/usr/bin/env python3
"""#15 - Chain Comparability Index. Compare tokens across chains: price, liquidity,

volume, tx count. Detect arbitrage and inflated-volume scams."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings

router = APIRouter(prefix="/api/v1/chain-compare", tags=["chain-comparability"])

BACKEND = settings.backend_url
DEXSCREENER = "https://api.dexscreener.com/latest/dex/search"

CHAINS = ["ethereum", "bsc", "base", "arbitrum", "polygon", "avalanche", "optimism", "solana"]


class CrossChainListing(BaseModel):
    chain: str
    price_usd: float
    liquidity_usd: float
    volume_24h: float
    volume_liq_ratio: float = 0
    tx_count_24h: int = 0
    dex: str
    age_days: int = 0
    suspicious: bool = False
    suspicion_reason: str = ""


async def _get_token_chain_listings(symbol: str) -> list[dict]:
    """Find the same token across multiple chains via DexScreener."""
    listings: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{DEXSCREENER}?q={symbol}")
            if resp.status_code != 200:
                return listings
            pairs = resp.json().get("pairs", [])
            for p in pairs[:50]:
                chain_id = p.get("chainId", "unknown")
                if chain_id in CHAINS or chain_id == "solana":
                    listings.append(
                        {
                            "chain": chain_id,
                            "price_usd": float(p.get("priceUsd", 0) or 0),
                            "liquidity_usd": p.get("liquidity", {}).get("usd", 0) or 0,
                            "volume_24h": float(p.get("volume", {}).get("h24", 0) or 0),
                            "tx_count_24h": p.get("txns", {}).get("h24", {}).get("buys", 0)
                            + p.get("txns", {}).get("h24", {}).get("sells", 0),
                            "dex": p.get("dexId", "?"),
                            "pair_created": p.get("pairCreatedAt", 0),
                        }
                    )
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"Chain listing fetch failed: {e}")
    return listings


def _detect_suspicious(listings: list[dict]) -> list[dict]:
    """Flag suspicious listings: inflated volume, price disconnects."""
    if len(listings) < 2:
        return listings

    avg_vol_liq = sum(line.get("volume_24h", 0) / max(line.get("liquidity_usd", 1), 1) for line in listings) / len(
        listings
    )
    avg_price = sum(line["price_usd"] for line in listings if line["price_usd"] > 0) / max(
        len([line for line in listings if line["price_usd"] > 0]), 1
    )

    for line in listings:
        v_ratio = line.get("volume_24h", 0) / max(line.get("liquidity_usd", 1), 1)
        line["volume_liq_ratio"] = round(v_ratio, 2)

        if v_ratio > avg_vol_liq * 3:
            line["suspicious"] = True
            line["suspicion_reason"] = "Inflated volume vs liquidity"

        if line["price_usd"] > 0 and avg_price > 0:
            price_dev = abs(line["price_usd"] - avg_price) / avg_price
            if price_dev > 0.5:
                line["suspicious"] = True
                existing = line.get("suspicion_reason", "")
                line["suspicion_reason"] = (
                    existing + "; " if existing else ""
                ) + f"Price disconnect ({price_dev:.0%} off average)"

    return listings


@router.get("/token/{symbol}")
async def compare_token_chains(symbol: str) -> dict:
    """Compare a token's listings across all chains. Flags suspicious activity."""
    listings = await _get_token_chain_listings(symbol)
    listings = _detect_suspicious(listings)

    suspicious = [line for line in listings if line.get("suspicious")]
    chains_active = len({line["chain"] for line in listings})

    return {
        "symbol": symbol,
        "chains_active": chains_active,
        "total_listings": len(listings),
        "suspicious_listings": len(suspicious),
        "listings": listings,
        "summary": (
            f"{symbol} trades on {chains_active} chains with {len(listings)} DEX listings. "
            f"{len(suspicious)} listings show suspicious patterns."
        ),
    }


@router.get("/arbitrage")
async def find_arbitrage_opportunities(
    min_discrepancy: float = Query(2.0, ge=0.5, description="Minimum % price difference"),
    limit: int = Query(10, le=25),
) -> dict:
    """Find tokens with large price discrepancies across chains (arbitrage signals)."""
    # Scan top tokens for price discrepancies
    opportunities: list[dict] = []
    popular = ["ETH", "USDC", "USDT", "MATIC", "ARB", "OP", "LINK", "UNI", "AAVE", "SNX"]

    for symbol in popular[:8]:
        listings = await _get_token_chain_listings(symbol)
        prices = [(line["chain"], line["price_usd"]) for line in listings if line["price_usd"] > 0]
        if len(prices) >= 2:
            prices.sort(key=lambda x: x[1])
            low_chain, low_price = prices[0]
            high_chain, high_price = prices[-1]
            if low_price > 0:
                discrepancy = ((high_price - low_price) / low_price) * 100
                if discrepancy > min_discrepancy:
                    opportunities.append(
                        {
                            "symbol": symbol,
                            "lowest": {"chain": low_chain, "price": low_price},
                            "highest": {"chain": high_chain, "price": high_price},
                            "discrepancy_pct": round(discrepancy, 2),
                        }
                    )

    opportunities.sort(key=lambda o: o["discrepancy_pct"], reverse=True)
    return {"opportunities": opportunities[:limit], "scan_time": "realtime"}
