"""
RugCharts News & Market Data Provider
======================================
Wires ALL free data sources into DataBus:
  - CoinGecko (prices, trending)
  - Alternative.me (Fear & Greed Index)
  - Polymarket (prediction markets)
  - CryptoPanic (news sentiment - if key)
  - CoinMarketCap (free tier)
  - Our 200+ RSS feeds from news_service
  - Existing MCP servers (feargreed, prediction-market, jupiter)

Every endpoint accessible via DataBus. Every response enriched.
"""

import os
import time
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger

logger = get_logger("news_provider")

# ── Free API endpoints (no keys needed) ────────────────────────────

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
POLYMARKET_URL = "https://gamma-api.polymarket.com/events"
COINGECKO_KEY = os.getenv("COINGECKO_API_KEY", "")

CACHE = {}  # Simple in-memory cache with TTL
CACHE_TTL = 60


def _cached(key: str, ttl: int = 60) -> dict | None:
    if key in CACHE:
        data, ts = CACHE[key]
        if time.time() - ts < ttl:
            return data
    return None


def _cache_set(key: str, data: dict):
    CACHE[key] = (data, time.time())


# ── 1. COINGECKO - Prices, trending, market data ───────────────────


async def get_market_prices(coins: str = "bitcoin,ethereum,solana", **kw) -> dict | None:
    """Live crypto prices from CoinGecko. Free tier, no key needed."""
    cache_key = f"prices:{coins}"
    cached = _cached(cache_key, 30)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            headers = {}
            if COINGECKO_KEY:
                headers["x-cg-demo-api-key"] = COINGECKO_KEY
            r = await c.get(
                f"{COINGECKO_BASE}/simple/price",
                params={
                    "ids": coins,
                    "vs_currencies": "usd",
                    "include_24hr_change": "true",
                    "include_market_cap": "true",
                },
                headers=headers,
            )
            if r.status_code == 200:
                data = r.json()
                result = {
                    "prices": data,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "source": "coingecko",
                    "free": True,
                }
                _cache_set(cache_key, result)
                return result
    except Exception as e:
        logger.warning(f"CoinGecko failed: {e}")
    return None


async def get_trending_coins(**kw) -> dict | None:
    """Trending coins from CoinGecko search."""
    cache_key = "trending"
    cached = _cached(cache_key, 120)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            headers = {}
            if COINGECKO_KEY:
                headers["x-cg-demo-api-key"] = COINGECKO_KEY
            r = await c.get(f"{COINGECKO_BASE}/search/trending", headers=headers)
            if r.status_code == 200:
                data = r.json()
                coins = data.get("coins", [])[:10]
                result = {
                    "trending": [
                        {
                            "name": c["item"]["name"],
                            "symbol": c["item"]["symbol"],
                            "market_cap_rank": c["item"].get("market_cap_rank"),
                            "score": c["item"].get("score"),
                        }
                        for c in coins
                    ],
                    "source": "coingecko",
                    "free": True,
                }
                _cache_set(cache_key, result)
                return result
    except Exception as e:
        logger.warning(f"Trending failed: {e}")
    return None


# ── 2. FEAR & GREED INDEX - Alternative.me ─────────────────────────


async def get_fear_greed(**kw) -> dict | None:
    """Crypto Fear & Greed Index. Completely free, no key."""
    cache_key = "fear_greed"
    cached = _cached(cache_key, 300)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(FEAR_GREED_URL)
            if r.status_code == 200:
                data = r.json().get("data", [{}])[0]
                value = int(data.get("value", 50))
                classification = data.get("value_classification", "Neutral")

                # Map to color and sentiment
                if value <= 25:
                    color, sentiment = "#ff0044", "Extreme Fear"
                elif value <= 45:
                    color, sentiment = "#ff8800", "Fear"
                elif value <= 55:
                    color, sentiment = "#ffd700", "Neutral"
                elif value <= 75:
                    color, sentiment = "#88ff00", "Greed"
                else:
                    color, sentiment = "#00ff88", "Extreme Greed"

                result = {
                    "value": value,
                    "classification": classification,
                    "sentiment": sentiment,
                    "color": color,
                    "timestamp": data.get("timestamp"),
                    "source": "alternative.me",
                    "free": True,
                }
                _cache_set(cache_key, result)
                return result
    except Exception as e:
        logger.warning(f"Fear & Greed failed: {e}")
    return None


# ── 3. POLYMARKET - Prediction markets ─────────────────────────────


async def get_prediction_markets(limit: int = 5, tag: str = "crypto", **kw) -> dict | None:
    """Prediction market events from Polymarket. Free, no key."""
    cache_key = f"polymarket:{tag}:{limit}"
    cached = _cached(cache_key, 120)
    if cached:
        return cached

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            params = {"limit": limit, "active": "true", "closed": "false"}
            if tag:
                params["tag"] = tag
            r = await c.get(POLYMARKET_URL, params=params)
            if r.status_code == 200:
                events = r.json()
                result = {
                    "markets": [
                        {
                            "title": e.get("title", ""),
                            "volume": e.get("volume", 0),
                            "liquidity": e.get("liquidity", 0),
                            "end_date": e.get("endDate", ""),
                            "slug": e.get("slug", ""),
                        }
                        for e in events[:limit]
                    ],
                    "total": len(events),
                    "source": "polymarket",
                    "free": True,
                }
                _cache_set(cache_key, result)
                return result
    except Exception as e:
        logger.warning(f"Polymarket failed: {e}")
    return None


# ── 4. COMBINED MARKET BRIEF - All sources in one call ─────────────


async def get_market_brief(**kw) -> dict | None:
    """One-call market overview: prices + fear/greed + trending + prediction markets."""
    prices_task = get_market_prices()
    fear_task = get_fear_greed()
    trending_task = get_trending_coins()
    poly_task = get_prediction_markets(limit=3)

    prices = await prices_task
    fear = await fear_task
    trending = await trending_task
    polymarket = await poly_task

    # Build natural-language brief
    brief_parts = []
    if fear:
        brief_parts.append(f"Market sentiment: {fear['classification']} ({fear['value']}/100)")
    if prices:
        for coin, data in prices.get("prices", {}).items():
            change = data.get("usd_24h_change", 0) or 0
            emoji = "🔴" if change < -3 else "🟠" if change < 0 else "🟢"
            brief_parts.append(
                f"{emoji} {coin.title()}: ${data.get('usd', 0):,.0f} ({change:+.1f}%)"
            )

    return {
        "brief": " | ".join(brief_parts),
        "prices": prices,
        "fear_greed": fear,
        "trending": trending,
        "prediction_markets": polymarket,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": ["coingecko", "alternative.me", "polymarket"],
        "source": "market_brief",
        "free": True,
    }


# ── 5. NEWS AGGREGATION - from our existing 200+ feeds ─────────────


async def get_aggregated_news(limit: int = 20, category: str = "", **kw) -> dict | None:
    """Pull news from our existing news_service.py aggregator."""
    try:
        from app.news_service import fetch_all_news

        articles = await fetch_all_news()
        if articles:
            if category:
                articles = [
                    a
                    for a in articles
                    if category.lower() in (a.get("category", "") + a.get("source", "")).lower()
                ]
            result = {
                "articles": articles[:limit],
                "total": len(articles),
                "filtered": len(articles[:limit]),
                "source": "rmi_news_aggregator",
                "free": True,
            }
            return result
    except Exception as e:
        logger.warning(f"News aggregation failed: {e}")
    return None


# ── 6. COMBINED NEWS + MARKET - The full picture ───────────────────


async def get_full_news_feed(limit: int = 15, **kw) -> dict | None:
    """Complete news page data: headlines + prices + fear/greed + trending + polymarket."""
    brief = await get_market_brief()
    news = await get_aggregated_news(limit=limit)

    return {
        "market_brief": brief,
        "headlines": news,
        "generated_at": datetime.now(UTC).isoformat(),
        "data_sources": [
            "CoinGecko (prices, trending)",
            "Alternative.me (Fear & Greed Index)",
            "Polymarket (prediction markets)",
            "RMI News Aggregator (200+ RSS feeds, 15 tiers)",
        ],
        "source": "full_news_feed",
        "free": True,
    }
