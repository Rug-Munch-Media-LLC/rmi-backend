"""Caching layer for the news provider: in-memory fallback + Redis."""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

import redis.asyncio as aioredis

from app.core.logging import get_logger
from app.databus.news_provider.helpers import NEWS_CACHE_KEY

logger = get_logger("news_provider")

# ── Redis config ───────────────────────────────────────────────────
REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None

CACHE = {}  # In-memory fallback cache with TTL


def _cached(key: str, ttl: int = 60) -> dict[str, Any] | None:
    if key in CACHE:
        data, ts = CACHE[key]
        if time.time() - ts < ttl:
            return data
    return None


def _cache_set(key: str, data: dict[str, Any]) -> None:
    CACHE[key] = (data, time.time())


async def _get_redis() -> aioredis.Redis | None:
    """Get async Redis connection."""
    try:
        r = aioredis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            password=REDIS_PASSWORD,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=3,
        )
        await r.ping()
        return r
    except Exception as e:
        logger.debug(f"Redis unavailable: {e}")
        return None


async def _redis_get(key: str) -> dict[str, Any] | None:
    """Read JSON from Redis."""
    try:
        r = await _get_redis()
        if r:
            data = await r.get(key)
            await r.close()
            if data:
                return json.loads(data)
    except Exception as e:
        logger.debug(f"Redis get failed: {e}")
    return None


async def _redis_set(key: str, data: dict[str, Any], ttl: int = 300) -> None:
    """Write JSON to Redis with TTL."""
    try:
        r = await _get_redis()
        if r:
            await r.setex(key, ttl, json.dumps(data, default=str))
            await r.close()
            logger.debug(f"Redis set {key} TTL={ttl}s")
    except Exception as e:
        logger.debug(f"Redis set failed: {e}")


# ── 8/9. PUBLIC READ ENDPOINTS (serve from Redis, instant) ─────────


async def get_aggregated_news(limit: int = 20, category: str = "", **kw) -> dict[str, Any] | None:
    """Get news articles. Reads from Redis cache (instant)."""
    cached = await _redis_get(NEWS_CACHE_KEY)
    if cached:
        headlines = cached.get("headlines", {})
        articles = headlines.get("articles", []) if isinstance(headlines, dict) else []
        if category:
            articles = [
                a for a in articles if category.lower() in (a.get("category", "") + a.get("source", "")).lower()
            ]
        articles = articles[:limit]
        return {
            "articles": articles,
            "total": len(articles),
            "filtered": len(articles),
            "source": "rmi_news_aggregator",
            "cached": True,
            "free": True,
        }

    # Cold cache — return empty gracefully
    return {
        "articles": [],
        "total": 0,
        "filtered": 0,
        "source": "rmi_news_aggregator",
        "cached": False,
        "free": True,
    }


async def get_full_news_feed(limit: int = 15, **kw) -> dict[str, Any] | None:
    """Complete news page data. ALWAYS reads from Redis.

    On cache miss, returns fast API data + market brief immediately
    and triggers background refresh.
    """
    from datetime import UTC, datetime

    from app.core.logging import get_logger
    from app.databus.news_provider.fetchers import _fetch_fast_api_news, get_market_brief, refresh_news_cache
    from app.databus.news_provider.helpers import TOP_50_FEEDS

    logger = get_logger("news_provider")

    # Try Redis cache first
    cached = await _redis_get(NEWS_CACHE_KEY)
    if cached:
        # Trim headlines to requested limit
        headlines = cached.get("headlines", {})
        if isinstance(headlines, dict) and "articles" in headlines:
            headlines["articles"] = headlines["articles"][:limit]
            headlines["total"] = min(headlines.get("total", 0), limit)
        cached["headlines"] = headlines
        return cached

    # Cache miss — do a fast response with API news only
    logger.info("News cache miss — returning fast API data + triggering background refresh")

    # Kick off background refresh (fire-and-forget; task ref kept to satisfy RUF006)
    _refresh_task = asyncio.create_task(refresh_news_cache())

    # Get market brief (fast, parallel)
    market_brief_task = asyncio.create_task(get_market_brief())

    # Fast API news (3-5 seconds)
    try:
        api_articles = await asyncio.wait_for(
            _fetch_fast_api_news(limit=20),
            timeout=5.0,
        )
    except (TimeoutError, Exception):
        api_articles = []

    market_brief = await market_brief_task

    return {
        "market_brief": market_brief,
        "headlines": {
            "articles": api_articles[:limit],
            "total": len(api_articles),
            "source": "rmi_news_aggregator",
            "free": True,
        },
        "generated_at": datetime.now(UTC).isoformat(),
        "data_sources": [
            "CoinGecko (news API, prices, trending)",
            "CryptoPanic (news sentiment)",
            "Alternative.me (Fear & Greed Index)",
            "Polymarket (prediction markets)",
            f"RSS ({len(TOP_50_FEEDS)} feeds — refreshing in background)",
        ],
        "source": "full_news_feed",
        "free": True,
        "refreshing": True,
    }
