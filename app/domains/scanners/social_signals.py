import asyncio
import os
from typing import Any

from app.apify_tools import logger

# CoinGecko trending + social signal enrichment
# Uses existing COINGECKO_API_KEY from .env
# Free tier: 10K calls/month, no additional cost


async def _check_coingecko_trending() -> dict[str, Any] | None:
    """CoinGecko trending - top-15 most searched coins (social signal).

    Tracks what retail is actively searching for - strong leading indicator
    of pump-and-dump targets. Free tier, uses existing API key.
    Cost: $0 (existing CoinGecko free tier).
    """
    api_key = os.getenv("COINGECKO_API_KEY", "")
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {}
            if api_key:
                headers["x-cg-demo-api-key"] = api_key

            result = await asyncio.wait_for(
                client.get("https://api.coingecko.com/api/v3/search/trending", headers=headers),
                timeout=10.0,
            )
            if result.status_code != 200:
                return None

            data = result.json()
            coins = data.get("coins", [])[:15]
            if not coins:
                return None

            trending = []
            for c in coins:
                item = c.get("item", {})
                trending.append(
                    {
                        "name": item.get("name"),
                        "symbol": item.get("symbol"),
                        "market_cap_rank": item.get("market_cap_rank"),
                        "score": item.get("score"),
                    }
                )

            return {
                "trending_count": len(trending),
                "top_3": trending[:3],
                "all_trending": trending,
                "data_source": "coingecko_trending",
            }
    except (TimeoutError, Exception) as e:
        logger.warning(f"CoinGecko trending failed: {e}")
        return None
