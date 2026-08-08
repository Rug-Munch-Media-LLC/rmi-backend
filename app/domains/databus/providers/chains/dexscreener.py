"""
DexScreener providers — free, no API key required.

Provides token prices, metadata, holders, trades, and top trader data
from DexScreener's public API.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers.dexscreener")


async def _dexscreener_price(**kwargs) -> dict[str, Any] | None:
    """DexScreener — free, no key."""
    token = kwargs.get("mint", "") or kwargs.get("token", "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_token_metadata(mint: str = "", **kw) -> dict[str, Any] | None:
    """Token metadata from DexScreener (free)."""
    if not mint:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_holders(mint: str = "", **kw) -> dict[str, Any] | None:
    """Top holders from DexScreener (free)."""
    if not mint:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}/holders")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_trades(token: str = "", chain: str = "solana", **kw) -> dict[str, Any] | None:
    """Live trades from DexScreener (free)."""
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.dexscreener.com/token-pairs/v1/{chain}/{token}",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_top_traders(token: str = "", chain: str = "solana", **kw) -> dict[str, Any] | None:
    """Top profitable traders for a token from DexScreener (free)."""
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.dexscreener.com/token-pairs/v1/{chain}/{token}/traders",
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None
