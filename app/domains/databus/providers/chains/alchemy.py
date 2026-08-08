"""
Alchemy providers — token balances and metadata (freemium, 300M compute/mo).
"""

from __future__ import annotations
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.alchemy")


async def _alchemy_token_balances(address: str = "", network: str = "eth-mainnet", **kw) -> dict | None:
    """Alchemy — get all token balances for an address."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        url = f"https://{network}.g.alchemy.com/v2/{api_key}"
        payload = {
            "jsonrpc": "2.0",
            "method": "alchemy_getTokenBalances",
            "params": [address, "erc20"],
            "id": 1,
        }
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json=payload)
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _alchemy_token_metadata(address: str = "", network: str = "eth-mainnet", **kw) -> dict | None:
    """Alchemy — get token metadata (name, symbol, decimals)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        url = f"https://{network}.g.alchemy.com/v2/{api_key}"
        payload = {
            "jsonrpc": "2.0",
            "method": "alchemy_getTokenMetadata",
            "params": [address],
            "id": 1,
        }
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload)
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
