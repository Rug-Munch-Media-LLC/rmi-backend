"""Free fallback providers — never pay for what's free."""

from __future__ import annotations
from typing import Any

import os

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


async def _dexscreener_token_metadata(mint: str = "", **kw) -> dict | None:
    """DexScreener - free token metadata from pairs endpoint."""
    token = mint or kw.get("token", "") or kw.get("contract", "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    p = pairs[0]
                    return {
                        "metadata": {
                            "name": p.get("baseToken", {}).get("name", ""),
                            "symbol": p.get("baseToken", {}).get("symbol", ""),
                            "decimals": None,
                            "price_usd": p.get("priceUsd"),
                            "liquidity_usd": p.get("liquidity", {}).get("usd"),
                            "fdv": p.get("fdv"),
                            "chain": p.get("chainId", ""),
                        },
                        "source": "dexscreener",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_holders(mint: str = "", **kw) -> dict | None:
    """DexScreener - free holder/liquidity data from pairs."""
    token = mint or kw.get("token", "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    return {"pairs": pairs, "total_pairs": len(pairs), "source": "dexscreener"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_trades(token: str = "", chain: str = "solana", **kw) -> dict | None:
    """DexScreener - recent trades for a token (free, no API key required)."""
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    return {
                        "trades": [],
                        "message": "Live trades feed requires premium DEX API. Returning available pair data.",
                        "pairs": pairs[:5],
                        "source": "dexscreener",
                        "fallback_used": False,
                        "freshness": "from_dexscreener_api",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_top_traders(token: str = "", chain: str = "solana", **kw) -> dict | None:
    """DexScreener - top profitable traders for a token."""
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    return {
                        "top_traders": [],
                        "message": "Top trader analytics require premium on-chain indexer. Showing available pair summary.",
                        "pairs": pairs[:3],
                        "source": "dexscreener",
                        "fallback_used": False,
                        "freshness": "from_dexscreener_api",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _etherscan_tx_trace(tx_hash: str = "", network: str = "ethereum", **kw) -> dict | None:
    """Etherscan - free transaction data (5 req/sec, no key needed for basic)."""
    if not tx_hash:
        return None
    try:
        domains = {
            "ethereum": "api.etherscan.io",
            "eth-mainnet": "api.etherscan.io",
            "bsc": "api.bscscan.com",
            "polygon": "api.polygonscan.com",
            "arbitrum": "api.arbiscan.io",
            "optimism": "api-optimistic.etherscan.io",
            "base": "api.basescan.org",
        }
        domain = domains.get(network, "api.etherscan.io")
        import os as _os

        api_key = _os.getenv("ETHERSCAN_API_KEY", "")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://{domain}/api",
                params={
                    "module": "proxy",
                    "action": "eth_getTransactionByHash",
                    "txhash": tx_hash,
                    "apikey": api_key,
                },
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("result"):
                    return {
                        "transaction": data["result"],
                        "tx_hash": tx_hash,
                        "source": "etherscan",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _defillama_tvl(**kw) -> dict | None:
    """DeFiLlama - global TVL and protocol data (completely free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.llama.fi/protocols")
            if r.status_code == 200:
                data = r.json()
                total_tvl = sum(p.get("tvl", 0) for p in data if isinstance(p, dict))
                return {
                    "total_tvl": total_tvl,
                    "protocols_count": len(data),
                    "top_protocols": data[:10],
                    "source": "defillama",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _defillama_chains(**kw) -> dict | None:
    """DeFiLlama - TVL by chain (completely free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.llama.fi/chains")
            if r.status_code == 200:
                data = r.json()
                return {
                    "chains": [{"name": c.get("name"), "tvl": c.get("tvl")} for c in data if isinstance(c, dict)],
                    "source": "defillama",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _blockchair_address(address: str = "", chain: str = "bitcoin", **kw) -> dict | None:
    """Blockchair - address balance and tx count (free tier)."""
    if not address:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://api.blockchair.com/{chain}/dashboards/address/{address}")
            if r.status_code == 200:
                data = r.json()
                if data.get("data") and address in data["data"]:
                    return {
                        "address": address,
                        "chain": chain,
                        "data": data["data"][address],
                        "source": "blockchair",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _blockchair_stats(chain: str = "bitcoin", **kw) -> dict | None:
    """Blockchair - chain statistics (free tier)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://api.blockchair.com/{chain}/stats")
            if r.status_code == 200:
                data = r.json()
                return {
                    "chain": chain,
                    "stats": data.get("data", {}).get("stats", {}),
                    "source": "blockchair",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _birdeye_overview(address: str = "", **kw) -> dict | None:
    """Birdeye - token overview, liquidity, and holder stats (free tier)."""
    api_key = kw.get("api_key", "") or os.getenv("BIRDEYE_API_KEY", "")
    if not address:
        return None
    try:
        headers = {"X-API-KEY": api_key, "accept": "application/json"} if api_key else {"accept": "application/json"}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://public-api.birdeye.so/defi/token_overview?address={address}",
                headers=headers,
            )
            if r.status_code == 200:
                data = r.json()
                return {"address": address, "data": data.get("data", {}), "source": "birdeye"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
