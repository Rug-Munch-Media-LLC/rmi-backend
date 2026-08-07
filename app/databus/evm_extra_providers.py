"""
BSC + Polygon DataBus Providers - Free public APIs, no key needed.
BscScan/PolygonScan free tier: balance, transactions, token transfers.
Rate limited to 1 req/5 sec per IP. No API key required for basic use.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.evm_extra")


async def _fetch_bsc_data(address: str = "", action: str = "balance", **kwargs) -> dict | None:
    """BSC intelligence via BscScan free public API."""
    import aiohttp

    apis = {
        "balance": f"https://api.bscscan.com/api?module=account&action=balance&address={address}&tag=latest",
        "txlist": f"https://api.bscscan.com/api?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&page=1&offset=10&sort=desc",
        "tokentx": f"https://api.bscscan.com/api?module=account&action=tokentx&address={address}&startblock=0&endblock=99999999&page=1&offset=10&sort=desc",
    }

    url = apis.get(action, apis["balance"])
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "1":
                    return {
                        "chain": "bsc",
                        "address": address,
                        "data": data.get("result"),
                        "source": "BscScan (free, no API key)",
                    }
                return {
                    "chain": "bsc",
                    "address": address,
                    "error": data.get("message", "No data"),
                    "source": "BscScan",
                }
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"BSC fetch failed: {e}")
    return None


async def _fetch_polygon_data(address: str = "", action: str = "balance", **kwargs) -> dict | None:
    """Polygon intelligence via PolygonScan free public API."""
    import aiohttp

    apis = {
        "balance": f"https://api.polygonscan.com/api?module=account&action=balance&address={address}&tag=latest",
        "txlist": f"https://api.polygonscan.com/api?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&page=1&offset=10&sort=desc",
        "tokentx": f"https://api.polygonscan.com/api?module=account&action=tokentx&address={address}&startblock=0&endblock=99999999&page=1&offset=10&sort=desc",
    }

    url = apis.get(action, apis["balance"])
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                if data.get("status") == "1":
                    return {
                        "chain": "polygon",
                        "address": address,
                        "data": data.get("result"),
                        "source": "PolygonScan (free, no API key)",
                    }
                return {
                    "chain": "polygon",
                    "address": address,
                    "error": data.get("message", "No data"),
                    "source": "PolygonScan",
                }
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Polygon fetch failed: {e}")
    return None
