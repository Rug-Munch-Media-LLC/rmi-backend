"""Price providers for DataBus."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.databus.vault import resolve_key

logger = get_logger(__name__)


async def _local_token_price(**kwargs) -> dict[str, Any] | None:
    """Get price from our own data (Redis/ClickHouse)."""
    try:
        import json

        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        token = kwargs.get("token", "")
        if token:
            cached = r.get(f"price:{token.lower()}")
            if cached:
                return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _dexscreener_price(**kwargs) -> dict[str, Any] | None:
    """DexScreener - free, no key."""
    token = kwargs.get("mint", "") or kwargs.get("token", "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _coingecko_price(**kwargs) -> dict[str, Any] | None:
    """CoinGecko - free for low volume."""
    token = kwargs.get("mint", "") or kwargs.get("token", "")
    api_key = kwargs.get("api_key", "")
    try:
        headers = {"x-cg-pro-api-key": api_key} if api_key else {}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.coingecko.com/api/v3/simple/token_price/{token}", headers=headers)
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _moralis_price(**kwargs) -> dict[str, Any] | None:
    """Moralis - paid, high quality."""
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
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _birdeye_price(address: str = "", **kw) -> dict[str, Any] | None:
    """Birdeye - real-time token price (free tier)."""
    api_key = kw.get("api_key", "") or resolve_key("BIRDEYE_API_KEY")
    if not address:
        return None
    try:
        headers = {"X-API-KEY": api_key, "accept": "application/json"} if api_key else {"accept": "application/json"}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://public-api.birdeye.so/defi/price?address={address}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                return {
                    "address": address,
                    "price": data.get("data", {}).get("value"),
                    "source": "birdeye",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _solana_tracker_price(mint: str = "", **kw) -> dict[str, Any] | None:
    """Solana Tracker - real-time token price (2 keys, 5000 req/mo total)."""
    if not mint:
        return None
    try:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()
        data = await st.get_price(mint)
        if data:
            return {"mint": mint, "price": data, "source": "solana_tracker"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _solana_tracker_token(mint: str = "", **kw) -> dict[str, Any] | None:
    """Solana Tracker - detailed token metadata and stats."""
    if not mint:
        return None
    try:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()
        data = await st.get_token(mint)
        if data:
            return {"mint": mint, "data": data, "source": "solana_tracker"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _solana_tracker_trending(**kw) -> dict[str, Any] | None:
    """Solana Tracker - trending tokens on Solana."""
    try:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()
        data = await st.get_tokens_trending(limit=20)
        if data:
            return {"trending": data, "source": "solana_tracker"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None


async def _gmgn_trending(**kw) -> dict[str, Any] | None:
    """GMGN Agent API - trending tokens (market/rank)."""
    try:
        from app.databus.gmgn_provider import get_gmgn_provider

        chain = kw.get("chain", "solana")
        interval = kw.get("interval", "1h")
        limit = min(int(kw.get("limit", 20)), 100)
        data = await get_gmgn_provider().get_trending(chain=chain, interval=interval, limit=limit)
        if data:
            return {"trending": data, "source": "gmgn"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("provider error")
    return None
