"""Data-fetching + scoring helpers for the RugCharts router.

Split out of ``app/routers/rugcharts.py`` (degodding 2026-08-07) so the
router module only holds endpoint definitions. Sources: GeckoTerminal,
DexScreener, ClickHouse (chain-indexer OHLCV), Redis caching.
"""

from __future__ import annotations

import datetime
import json
import os
from typing import Any

import httpx
import redis

from app.core.logging import get_logger

logger = get_logger("rugcharts_helpers")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

CACHE_TTL = 120  # 2 minutes for trending, 5 minutes for OHLCV
OHLCV_TTL = 300
CH_OHLCV_TTL = 120  # 2 minutes for ClickHouse-backed candles

# ClickHouse (chain-indexer OHLCV materialized view) config — same env
# contract as the rest of the backend (scanner own-data engines).
CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "9000"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "") or ""
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DATABASE", "wallet_memory")

# GeckoTerminal network mapping
GECKO_NETWORKS = {
    "solana": "solana",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "arbitrum": "arbitrum",
    "tron": "tron",
    "polygon": "polygon_pos",
    "avalanche": "avax",
    "optimism": "optimism",
}

# Timeframe mapping for GeckoTerminal
GECKO_TIMEFRAMES = {
    "1m": ("minute", 1),
    "5m": ("minute", 5),
    "15m": ("minute", 15),
    "1h": ("hour", 1),
    "4h": ("hour", 4),
    "1d": ("day", 1),
    "7d": ("day", 1),
    "30d": ("day", 1),
}


def _r() -> Any:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD,
        decode_responses=True,
        socket_connect_timeout=2,
    )


async def _fetch_dexscreener_trending(chain: str, limit: int = 20) -> tuple[list[dict], bool]:
    """Fetch trending tokens from DexScreener with real price data."""
    cache_key = f"rugcharts:trending:{chain}:{limit}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached), False
        r.close()
    except (ConnectionError, TimeoutError, OSError, json.JSONDecodeError) as e:
        logger.debug(f"Redis cache read failed for {cache_key}: {e}")

    chain_map = {
        "solana": "solana",
        "ethereum": "ethereum",
        "base": "base",
        "bsc": "bsc",
        "arbitrum": "arbitrum",
        "tron": "tron",
    }
    dex_chain = chain_map.get(chain, chain)

    tokens = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Get boosted tokens (trending)
            r = await client.get("https://api.dexscreener.com/token-boosts/top/v1")
            if r.status_code == 200:
                data = r.json()
                items = data if isinstance(data, list) else data.get("tokens", [])
                for item in items:
