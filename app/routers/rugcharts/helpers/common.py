"""Shared infrastructure for rugcharts helpers: Redis cache, ClickHouse, constants.

Concurrency contract (2026-08-24 audit WIN-2):
- Redis access is fully async (``redis.asyncio``) over ONE shared
  connection pool — previously every cache touch built a fresh sync
  connection INSIDE async endpoints, blocking the event loop.
- ClickHouse queries run the sync ``clickhouse_driver`` client inside
  ``asyncio.to_thread``, bounded by a semaphore so a burst of requests
  cannot open unbounded connections. Previously these calls ran inline
  on the event loop and froze ALL endpoints during queries.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from app.core.logging import get_logger

logger = get_logger("rugcharts_helpers")

REDIS_HOST = os.getenv("REDIS_HOST", "rmi-redis")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

CACHE_TTL = 120  # trending + pair info
OHLCV_TTL = 300
CH_OHLCV_TTL = 120  # ClickHouse-backed candles
WHALE_TTL = 120
WHALE_MIN_USD = 10_000.0  # match indexer whale flag threshold (>= $10K)

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "9000"))
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "rmi")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "") or ""
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DATABASE", "wallet_memory")

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

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")
INTERNAL_API_BASE = os.getenv("RMI_INTERNAL_API_BASE", "http://localhost:8000")

RECENT_TRADES_TTL = 5  # seconds — tape freshness window

# ── Async Redis (pooled) ─────────────────────────────────────────────────

_redis_pool: Any = None
_redis_pool_lock: asyncio.Lock | None = None


async def _r() -> Any:
    """Shared lazy redis.asyncio client. Never raises on connect here."""
    global _redis_pool, _redis_pool_lock
    if _redis_pool is None:
        if _redis_pool_lock is None:
            _redis_pool_lock = asyncio.Lock()
        async with _redis_pool_lock:
            if _redis_pool is None:
                import redis.asyncio as aioredis

                _redis_pool = aioredis.ConnectionPool(
                    host=REDIS_HOST,
                    port=REDIS_PORT,
                    password=REDIS_PASSWORD,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    max_connections=32,
                )
    import redis.asyncio as aioredis

    return aioredis.Redis(connection_pool=_redis_pool)


async def _cache_get_json(key: str) -> Any | None:
    try:
        r = await _r()
        cached = await r.get(key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.debug(f"Redis cache read failed for {key}: {e}")
    return None


async def _cache_set_json(key: str, value: Any, ttl: int) -> None:
    try:
        r = await _r()
        await r.set(key, json.dumps(value), ex=ttl)
    except Exception as e:
        logger.debug(f"Redis cache write failed for {key}: {e}")


# ── ClickHouse (threaded, bounded) ────────────────────────────────────────

_CH_SEMAPHORE = asyncio.Semaphore(4)


async def _ch_query(sql: str, params: dict[str, Any]) -> list[tuple[Any, ...]]:
    """Run a parameterized ClickHouse query off the event loop."""
    try:
        from clickhouse_driver import Client as CHClient
    except ImportError:
        logger.debug("clickhouse_driver not installed — CH unavailable")
        return []

    def _run() -> list[tuple[Any, ...]]:
        client = CHClient(
            host=CLICKHOUSE_HOST,
            port=CLICKHOUSE_PORT,
            user=CLICKHOUSE_USER,
            password=CLICKHOUSE_PASSWORD,
            database=CLICKHOUSE_DB,
            connect_timeout=3,
            send_receive_timeout=10,
        )
        try:
            result: Any = client.execute(sql, params)
            return result
        finally:
            client.disconnect()

    async with _CH_SEMAPHORE:
        return await asyncio.to_thread(_run)
