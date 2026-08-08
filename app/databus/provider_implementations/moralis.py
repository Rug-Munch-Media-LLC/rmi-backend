"""Moralis Web3 AI Agents — Full API Suite."""

from __future__ import annotations
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

_MORALIS_BASE = "https://deep-index.moralis.io/api/v2.2"


async def _moralis_wallet_tokens(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get wallet token balances with metadata."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/{address}/erc20",
                params={"chain": chain},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"tokens": r.json(), "address": address, "chain": chain}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_nfts(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get wallet NFTs."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/{address}/nft",
                params={"chain": chain},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"nfts": r.json(), "address": address, "chain": chain}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_transactions(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get wallet transaction history."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        limit = kw.get("limit", 50)
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/{address}",
                params={"chain": chain, "limit": limit},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"transactions": r.json(), "address": address, "chain": chain}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_token_price(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get token price via Token API."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/erc20/{address}/price",
                params={"chain": chain},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"price": r.json(), "address": address, "chain": chain}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_token_metadata(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get token metadata."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/erc20/{address}",
                params={"chain": chain},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"metadata": r.json(), "address": address}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_net_worth(address: str = "", **kw) -> dict | None:
    """Moralis - wallet net worth across chains."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/wallets/{address}/net-worth",
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"net_worth": r.json(), "address": address}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_search_tokens(query: str = "", **kw) -> dict | None:
    """Moralis - search tokens by name/symbol/address."""
    api_key = kw.get("api_key", "")
    if not query or not api_key:
        return None
    try:
        limit = kw.get("limit", 10)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/search",
                params={"q": query, "filter": "token", "limit": limit},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"results": r.json(), "query": query}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
