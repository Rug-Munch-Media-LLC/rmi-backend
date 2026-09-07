"""Live market context fetcher (CoinGecko free API) for the chat endpoint.

Moved down from ``app/routers/chat/_market_fetch.py`` (de-godding phase 2;
import-boundary debt — the chat tool-use layer in ``app.domains`` shares this
fetcher, and ``app.domains`` must not import from ``app.routers``). It is
re-exported from the routers.chat package root so existing importers keep
working — ``app.domains.chat_tools._exec_market`` patches
``app.domains.chat_tools.market_fetch._fetch_market_context`` (its canonical
home now) and tests that patch the defining module's ``_get_client`` keep
working because the re-export is the SAME object.
"""

from __future__ import annotations

import json

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Free API endpoints for market context ──
COINGECKO_API = "https://api.coingecko.com/api/v3"

_http_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=10.0,
            headers={"User-Agent": "RugMunchIntelligence/1.0"},
        )
    return _http_client


async def _fetch_market_context() -> dict:
    """Fetch live market data for AI context injection."""
    try:
        client = await _get_client()
        resp = await client.get(
            f"{COINGECKO_API}/simple/price",
            params={
                "ids": "bitcoin,ethereum,solana",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "btc_price": data.get("bitcoin", {}).get("usd", 0),
                "btc_change_24h": data.get("bitcoin", {}).get("usd_24h_change", 0),
                "eth_price": data.get("ethereum", {}).get("usd", 0),
                "eth_change_24h": data.get("ethereum", {}).get("usd_24h_change", 0),
                "sol_price": data.get("solana", {}).get("usd", 0),
                "sol_change_24h": data.get("solana", {}).get("usd_24h_change", 0),
            }
    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning(f"CoinGecko price fetch failed: {e}")
    return {}
