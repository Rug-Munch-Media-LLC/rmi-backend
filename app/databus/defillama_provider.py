"""
DeFiLlama DataBus Provider - Free, unlimited DeFi analytics.
7,661 protocols across 350+ chains. No API key needed.
"""

from __future__ import annotations

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.defillama")


async def fetch_defillama_tvl(protocol: str | None = None, chain: str | None = None, limit: int = 20) -> dict:
    """Fetch TVL data from DeFiLlama - free, no auth, no rate limits for standard traffic."""
    import aiohttp

    results = {}

    async with aiohttp.ClientSession() as session:
        # All protocols
        try:
            async with session.get("https://api.llama.fi/protocols", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if protocol:
                        # Filter by protocol name
                        matches = [p for p in data if protocol.lower() in p.get("name", "").lower()][:limit]
                        results["protocols"] = matches
                    elif chain:
                        matches = [p for p in data if chain.lower() in [c.lower() for c in p.get("chains", [])]][:limit]
                        results["protocols"] = matches
                    else:
                        # Return top protocols by TVL
                        results["protocols"] = sorted(data, key=lambda x: x.get("tvl", 0) or 0, reverse=True)[:limit]
                    results["total"] = len(data)
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"DeFiLlama protocols fetch failed: {e}")
            results["protocols"] = []

        # Chain TVLs
        try:
            async with session.get("https://api.llama.fi/v2/chains", timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    chain_data = await resp.json()
                    results["chains"] = sorted(chain_data, key=lambda x: x.get("tvl", 0) or 0, reverse=True)[:limit]
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"DeFiLlama chains fetch failed: {e}")
            results["chains"] = []

        # Global TVL
        try:
            async with session.get(
                "https://api.llama.fi/v2/historicalChainTvl/All",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    results["historical_tvl"] = (await resp.json())[-30:]  # Last 30 days
        except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning(f"DeFiLlama historical TVL failed: {e}")

    results["source"] = "DeFiLlama (free, no auth)"
    results["url"] = "https://defillama.com"
    return results


async def fetch_pyth_prices(symbols: str | None = None, limit: int = 10) -> dict:
    """Fetch live prices from Pyth Network Hermes API - 125+ institutional publishers."""
    import aiohttp

    try:
        # Pyth Hermes REST API for latest price feeds
        url = "https://hermes.pyth.network/v2/updates/price/latest"
        params = {}
        if symbols:
            # Convert symbols to Pyth feed IDs (simplified - production would use lookup)
            params["ids"] = symbols

        async with (
            aiohttp.ClientSession() as session,
            session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp,
        ):
            if resp.status == 200:
                data = await resp.json()
                results = {
                    "prices": [],
                    "source": "Pyth Network (Hermes)",
                    "publishers": "125+ institutional (Jane Street, Wintermute, etc.)",
                    "rate_limit": "10 req / 10 seconds (free)",
                }
                for parsed in data.get("parsed", [])[:limit]:
                    price_info = parsed.get("price", {})
                    results["prices"].append(
                        {
                            "id": parsed.get("id", ""),
                            "price": float(price_info.get("price", 0)) * 10 ** float(price_info.get("expo", 0)),
                            "conf": float(price_info.get("conf", 0)) * 10 ** float(price_info.get("expo", 0)),
                            "publish_time": price_info.get("publish_time"),
                        }
                    )
                return results
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.warning(f"Pyth fetch failed: {e}")
        return {"error": str(e), "source": "Pyth Network (Hermes)"}

    return {"prices": [], "source": "Pyth Network (Hermes)"}
