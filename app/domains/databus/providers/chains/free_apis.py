"""
Free API providers — no API key required for basic usage.

Includes: CoinGecko, DeFiLlama, Blockchair, Etherscan.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.free_apis")


async def _coingecko_price(**kwargs) -> dict[str, Any] | None:
    """CoinGecko token price — delegates to the canonical implementation.

    The legacy ``simple/token_price/{token}`` URL put a contract address in
    the platform-id slot (the endpoint expects a platform id like
    ``ethereum`` plus a ``contract_addresses`` param) and always returned
    404/empty. ``free_security._free_market_coingecko`` maps chain →
    CoinGecko platform id and builds the params correctly — delegate to it
    so the legacy ``_coingecko_price`` name used by the ``token_price`` /
    ``market_overview`` chains returns real prices.
    """
    from app.domains.databus.providers.chains.free_security import _free_market_coingecko

    return await _free_market_coingecko(**kwargs)


async def _defillama_tvl(**kw) -> dict[str, Any] | None:
    """DeFiLlama — total TVL across all protocols (100% free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.llama.fi/protocols")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _defillama_chains(**kw) -> dict[str, Any] | None:
    """DeFiLlama — TVL breakdown by chain (100% free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.llama.fi/v2/chains")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _blockchair_address(address: str = "", chain: str = "bitcoin", **kw) -> dict[str, Any] | None:
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


async def _blockchair_stats(chain: str = "bitcoin", **kw) -> dict[str, Any] | None:
    """Blockchair — network statistics and mempool data (free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://api.blockchair.com/{chain}/stats")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _etherscan_tx_trace(tx_hash: str = "", network: str = "ethereum", **kw) -> dict[str, Any] | None:
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
