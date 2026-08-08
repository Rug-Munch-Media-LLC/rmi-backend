"""
Enhanced Daily Market Data - Price action, sentiment, security, whales.

Pulls from ALL our data sources to create comprehensive daily analysis.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("market_data")


async def get_price_action() -> dict[str, Any]:
    """Get real-time prices and 24h changes for major assets."""
    try:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()

        assets = {
            "solana": "So11111111111111111111111111111111111111112",
            "usdc": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        }
        prices = {}
        for name, mint in assets.items():
            r = await st.get_price(mint)
            if r:
                prices[name] = {"price": r.get("price", 0), "liquidity": r.get("liquidity", 0)}

        # Also get trending tokens
        trending = await st.get_tokens_trending(limit=5)

        return {"major_assets": prices, "trending": trending}
    except Exception as e:
        return {"error": str(e), "major_assets": {}, "trending": []}


async def get_fear_greed() -> dict[str, Any]:
    """Get Fear & Greed index from our local MCP."""
    try:
        # Use our crypto-feargreed-mcp
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.alternative.me/fng/?limit=2")
            if r.status_code == 200:
                data = r.json().get("data", [])
                if data:
                    current = data[0]
                    return {
                        "value": int(current.get("value", 50)),
                        "classification": current.get("value_classification", "Neutral"),
                        "timestamp": current.get("timestamp", ""),
                    }
    except (ConnectionError, OSError, TimeoutError):
        pass
    return {"value": 50, "classification": "Neutral"}


async def get_top_movers() -> dict[str, Any]:
    """Get top gainers and losers from CoinGecko."""
    try:
        key = os.getenv("COINGECKO_API_KEY", "")
        async with httpx.AsyncClient(timeout=10) as c:
            # Top gainers
            r = await c.get(
                "https://pro-api.coingecko.com/api/v3/search/trending",
                headers={"x-cg-pro-api-key": key} if key else {},
            )
            if r.status_code == 200:
                coins = r.json().get("coins", [])
                return {
                    "trending": [c.get("item", {}).get("name") for c in coins[:5]],
                    "trending_score": [c.get("item", {}).get("market_cap_rank") for c in coins[:5]],
                }
    except (ConnectionError, OSError, TimeoutError):
        pass
    return {"trending": [], "trending_score": []}


async def get_security_alerts() -> dict[str, Any]:
    """Get recent security incidents from our risk scanner + news."""
    alerts = []
    try:
        # Check recent hacks from known sources
        async with httpx.AsyncClient(timeout=10) as c:
            # Rekt News (crypto hacks database)
            r = await c.get("https://api.rekt.news/api/v2/leaderboard?limit=5")
            if r.status_code == 200:
                hacks = r.json()
                if isinstance(hacks, list):
                    for h in hacks[:3]:
                        alerts.append(
                            {
                                "type": "hack",
                                "project": h.get("name", "Unknown"),
                                "amount_lost": h.get("totalLost", ""),
                                "date": h.get("date", ""),
                            }
                        )
    except (ConnectionError, OSError, TimeoutError):
        pass

    try:
        # Check RugCheck for recent rug pulls
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.rugcheck.xyz/v1/stats/recent")
            if r.status_code == 200:
                rug_data = r.json()
                alerts.append(
                    {
                        "type": "rug_check",
                        "recent_scans": len(rug_data) if isinstance(rug_data, list) else 0,
                    }
                )
    except httpx.HTTPError:
        pass

    return {"alerts": alerts, "count": len(alerts)}


async def get_whale_activity() -> dict[str, Any]:
    """Get whale movement summary."""
    try:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()

        # Get trending tokens with high volume (whale activity indicator)
        trending = await st.get_tokens_trending(limit=10)
        if trending:
            high_volume = list(trending.get("data", trending.get("tokens", []))[:5])
            return {"trending_high_volume": len(high_volume), "chains": ["solana"]}
    except (AttributeError, ImportError):
        pass
    return {"trending_high_volume": 0}


async def get_prediction_markets() -> dict[str, Any]:
    """Get Polymarket odds for crypto-related markets."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://gamma-api.polymarket.com/events?tag=crypto&limit=5&active=true")
            if r.status_code == 200:
                events = r.json()
                markets = []
                for e in events[:5]:
                    markets.append(
                        {
                            "title": e.get("title", ""),
                            "volume": e.get("volume", 0),
                            "liquidity": e.get("liquidity", 0),
                        }
                    )
                return {"active_markets": len(markets), "top_markets": markets}
    except (ConnectionError, OSError, TimeoutError):
        pass
    return {"active_markets": 0, "top_markets": []}


async def get_daily_rundown_data() -> dict[str, Any]:
    """Get ALL daily data for the AI market rundown."""
    results = await asyncio.gather(
        get_price_action(),
        get_fear_greed(),
        get_top_movers(),
        get_security_alerts(),
        get_whale_activity(),
        get_prediction_markets(),
        return_exceptions=True,
    )

    return {
        "price_action": results[0] if not isinstance(results[0], Exception) else {},
        "fear_greed": results[1] if not isinstance(results[1], Exception) else {},
        "top_movers": results[2] if not isinstance(results[2], Exception) else {},
        "security_alerts": results[3] if not isinstance(results[3], Exception) else {},
        "whale_activity": results[4] if not isinstance(results[4], Exception) else {},
        "prediction_markets": results[5] if not isinstance(results[5], Exception) else {},
        "timestamp": datetime.now(UTC).isoformat(),
    }
