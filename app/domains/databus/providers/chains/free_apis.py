"""
Free API providers — no API key required for basic usage.

Includes: CoinGecko, DeFiLlama, Blockchair, Etherscan.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.free_apis")


async def _coingecko_price(**kwargs) -> dict | None:
    """CoinGecko — free for low volume."""
    token = kwargs.get("mint", "") or kwargs.get("token", "")
    api_key = kwargs.get("api_key", "")
    try:
        headers = {"x-cg-pro-api-key": api_key} if api_key else {}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.coingecko.com/api/v3/simple/token_price/{token}",
                headers=headers,
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _defillama_tvl(**kw) -> dict | None:
    """DeFiLlama — total TVL across all protocols (100% free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.llama.fi/protocols")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _defillama_chains(**kw) -> dict | None:
    """DeFiLlama — TVL breakdown by chain (100% free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.llama.fi/v2/chains")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _blockchair_address(address: str = "", chain: str = "bitcoin", **kw) -> dict | None:
    """Blockchair — address balance and tx count (free tier, no API key for basic)."""
    if not address:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://api.blockchair.com/{chain}/dashboards/address/{address}",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _blockchair_stats(chain: str = "bitcoin", **kw) -> dict | None:
    """Blockchair — network statistics and mempool data (free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://api.blockchair.com/{chain}/stats")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _etherscan_tx_trace(tx_hash: str = "", network: str = "ethereum", **kw) -> dict | None:
    """Etherscan — transaction trace (free tier, 5 req/s)."""
    if not tx_hash:
        return None
    api_key = kw.get("api_key", "")
    try:
        url = f"https://api.etherscan.io/api?module=transaction&action=gettxreceiptstatus&txhash={tx_hash}&apikey={api_key}"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
