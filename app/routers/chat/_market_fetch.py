"""Live market context fetcher (CoinGecko free API) for the chat endpoint.

Split out of ``app/routers/chat/__init__.py`` (de-godding phase 2,
behavior-preserving). Re-exported from the package root —
``app.domains.chat_tools._exec_market`` patches
``app.routers.chat._fetch_market_context`` and ``_get_client`` is patched by
tests on the defining module, so both names stay on the package root too.
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
