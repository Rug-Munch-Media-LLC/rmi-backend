"""
RugCharts News & Market Data Provider
=====================================
Redis-backed cached news aggregation. All endpoints serve from
Redis (instant response). A background task refreshes every 5 min.

Data sources:
  - CoinGecko (prices, trending)
  - Alternative.me (Fear & Greed Index)
  - Polymarket (prediction markets)
  - CryptoPanic (news sentiment - if key)
  - 75+ RSS feeds (CoinTelegraph, Decrypt, The Block, etc.)
  - Existing MCP servers (feargreed, prediction-market, jupiter)

Sub-modules:
  - fetchers:     news/market data fetching (APIs + RSS) and the
                  background cache refresher
  - normalizers:  dedupe, sorting, relevance ranking
  - cache:        in-memory fallback + Redis caching, public read endpoints
  - helpers:      shared constants (endpoints, feed registry) and utilities
"""

from __future__ import annotations

from app.databus.news_provider.cache import (
    NEWS_CACHE_KEY,
    _redis_get,
    _redis_set,
    get_aggregated_news,
    get_full_news_feed,
)
from app.databus.news_provider.fetchers import (
    _fetch_fast_api_news,
    _fetch_rss_articles,
    get_fear_greed,
    get_market_brief,
    get_market_prices,
    get_prediction_markets,
    get_trending_coins,
    refresh_news_cache,
)
from app.databus.news_provider.helpers import (
    COINGECKO_BASE,
    COINGECKO_KEY,
    CRYPTOPANIC_API_KEY,
    FEAR_GREED_URL,
    MARKET_BRIEF_KEY,
    MARKET_BRIEF_TTL,
    NEWS_CACHE_TTL,
    POLYMARKET_URL,
    TOP_50_FEEDS,
    hashlib_quick,
)

__all__ = [
    "COINGECKO_BASE",
    "COINGECKO_KEY",
    "CRYPTOPANIC_API_KEY",
    "FEAR_GREED_URL",
    "MARKET_BRIEF_KEY",
    "MARKET_BRIEF_TTL",
    "NEWS_CACHE_KEY",
    "NEWS_CACHE_TTL",
    "POLYMARKET_URL",
    "TOP_50_FEEDS",
    "_fetch_fast_api_news",
    "_fetch_rss_articles",
    "_redis_get",
    "_redis_set",
    "get_aggregated_news",
    "get_fear_greed",
    "get_full_news_feed",
    "get_market_brief",
    "get_market_prices",
    "get_prediction_markets",
    "get_trending_coins",
    "hashlib_quick",
    "refresh_news_cache",
]
