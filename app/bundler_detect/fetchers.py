"""
Bundler Detector - Data Fetchers
=================================
External API data retrieval for token metadata, holders, and buy transactions.
Supports DexScreener, Birdeye, and Solscan APIs.
"""

from __future__ import annotations
import logging
from typing import Any

import httpx

from app.bundler_detect.models import BIRDEYE_PUBLIC, DEXSCREENER_API

logger = logging.getLogger(__name__)

async def fetch_metadata(http: httpx.AsyncClient, address: str, chain: str) -> dict[str, Any]:
    """Fetch token metadata from DexScreener."""
    try:
        url = f"{DEXSCREENER_API}/tokens/{address}"
        resp = await http.get(url, timeout=10)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        pairs = data.get("pairs", [])
        if not pairs:
            return {}

        pair = pairs[0]
        return {
            "name": pair.get("baseToken", {}).get("name", ""),
            "symbol": pair.get("baseToken", {}).get("symbol", ""),
            "decimals": pair.get("baseToken", {}).get("decimals"),
            "price_usd": pair.get("priceUsd", ""),
            "liquidity_usd": pair.get("liquidity", {}).get("usd", 0),
            "fdv": pair.get("fdv", 0),
            "pair_address": pair.get("pairAddress", ""),
            "dex": pair.get("dexId", ""),
            "url": pair.get("url", ""),
            "social": {
                "twitter": pair.get("info", {}).get("twitter", ""),
                "website": pair.get("info", {}).get("website", ""),
                "telegram": pair.get("info", {}).get("telegram", ""),
            },
            "creation_block": None,
        }
    except Exception as e:
        logger.debug(f"Metadata fetch error: {e}")
        return {}

async def fetch_holders(
    http: httpx.AsyncClient,
    address: str,
    chain: str,
    birdeye_api_key: str = "",
) -> list[dict[str, Any]]:
    """Fetch top holders from Birdeye public API or Solscan."""
    try:
        if chain == "solana":
            return await _fetch_solana_holders(http, address, birdeye_api_key)
        return await _fetch_evm_holders(http, address, birdeye_api_key)
    except Exception as e:
        logger.debug(f"Holder fetch error: {e}")
        return []

async def _fetch_solana_holders(
    http: httpx.AsyncClient,
    address: str,
    birdeye_api_key: str = "",
) -> list[dict[str, Any]]:
    """Fetch Solana token holders via Birdeye, fallback to Solscan."""
    try:
        url = f"{BIRDEYE_PUBLIC}/defi/holder/tokenlist?tokenAddress={address}&limit=100"
        headers = {"Accept": "application/json"}
        if birdeye_api_key:
            headers["X-API-KEY"] = birdeye_api_key

        resp = await http.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", {}).get("items", [])
            return [
                {
                    "address": h.get("holder", ""),
                    "amount": h.get("amount", 0),
                    "percentage": h.get("percent", 0),
                }
                for h in items
            ]
    except Exception as e:
        logger.debug(f"Solana holder fetch error: {e}")

    # Fallback: Solscan free API
    try:
        url = f"https://public-api.solscan.io/token/holders?tokenAddress={address}&limit=100&offset=0"
        resp = await http.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data if isinstance(data, list) else data.get("data", [])
            return [
                {
                    "address": h.get("owner", h.get("address", "")),
                    "amount": h.get("amount", h.get("balance", 0)),
                    "percentage": h.get("percentage", h.get("percent", 0)),
                }
                for h in items
            ]
    except Exception as e:
        logger.debug(f"Solscan holder fallback error: {e}")

    return []

async def _fetch_evm_holders(
    http: httpx.AsyncClient,
    address: str,
    birdeye_api_key: str = "",
) -> list[dict[str, Any]]:
    """Fetch EVM token holders via Birdeye public API."""
    try:
        url = f"{BIRDEYE_PUBLIC}/defi/holder/tokenlist?tokenAddress={address}&limit=100"
        headers = {"Accept": "application/json"}
        if birdeye_api_key:
            headers["X-API-KEY"] = birdeye_api_key

        resp = await http.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", {}).get("items", [])
            return [
                {
                    "address": h.get("holder", ""),
                    "amount": h.get("amount", 0),
                    "percentage": h.get("percent", 0),
                }
                for h in items
            ]
    except Exception as e:
        logger.debug(f"EVM holder fetch error: {e}")

    return []

async def fetch_buys(http: httpx.AsyncClient, address: str, chain: str) -> list[dict[str, Any]]:
    """Fetch recent buy transactions for the token."""
    _ = chain  # DexScreener is chain-agnostic
    buys: list[dict[str, Any]] = []
    try:
        url = f"{DEXSCREENER_API}/tokens/{address}"
        resp = await http.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            pairs = data.get("pairs", [])
            for pair in pairs[:5]:
                txns = pair.get("txns", {})
                m5 = txns.get("m5", {}) or {}
                h1 = txns.get("h1", {}) or {}
                h6 = txns.get("h6", {}) or {}
                buys.append(
                    {
                        "type": "buy",
                        "m5_buys": m5.get("buys", 0),
                        "m5_sells": m5.get("sells", 0),
                        "h1_buys": h1.get("buys", 0),
                        "h1_sells": h1.get("sells", 0),
                        "h6_buys": h6.get("buys", 0),
                        "h6_sells": h6.get("sells", 0),
                        "pair_address": pair.get("pairAddress", ""),
                        "creation_block": None,
                    }
                )

                volume_m5 = pair.get("volume", {}).get("m5", 0) or 0
                if m5.get("buys", 0) > 0:
                    avg_buy = float(volume_m5) / max(1, m5.get("buys", 1))
                    buys[-1]["avg_buy_value"] = avg_buy
    except Exception as e:
        logger.debug(f"Buy fetch error: {e}")

    return buys
