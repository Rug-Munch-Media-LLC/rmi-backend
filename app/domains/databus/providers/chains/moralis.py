"""
Moralis providers — Web3 data API suite (freemium tier).

Covers: wallet tokens, NFTs, transactions, token price/metadata,
net worth, and token search.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.moralis")


async def _moralis_price(**kwargs) -> dict | None:
    """Moralis — paid, high quality token price."""
    token = kwargs.get("token", "")
    api_key = kwargs.get("api_key", "")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://deep-index.moralis.io/api/v2/erc20/{token}/price",
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_tokens(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis — wallet token balances."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://deep-index.moralis.io/api/v2/{address}/erc20",
                headers={"X-API-Key": api_key},
                params={"chain": chain},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_nfts(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis — wallet NFT holdings."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://deep-index.moralis.io/api/v2/{address}/nft",
                headers={"X-API-Key": api_key},
                params={"chain": chain, "format": "decimal"},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_transactions(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis — wallet transaction history."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://deep-index.moralis.io/api/v2/{address}",
                headers={"X-API-Key": api_key},
                params={"chain": chain},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_token_price(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis — token price (specific endpoint)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://deep-index.moralis.io/api/v2/erc20/{address}/price",
                headers={"X-API-Key": api_key},
                params={"chain": chain},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_token_metadata(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis — token metadata (name, symbol, decimals)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://deep-index.moralis.io/api/v2/erc20/metadata",
                headers={"X-API-Key": api_key},
                params={"chain": chain, "addresses[]": address},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_net_worth(address: str = "", **kw) -> dict | None:
    """Moralis — wallet net worth across chains."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://deep-index.moralis.io/api/v2/{address}/wallet/net-worth",
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_search_tokens(query: str = "", **kw) -> dict | None:
    """Moralis — search tokens by name/symbol/address."""
    api_key = kw.get("api_key", "")
    if not query or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://deep-index.moralis.io/api/v2/tokens/search",
                headers={"X-API-Key": api_key},
                params={"q": query},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
