"""Data-fetching + scoring helpers for the RugCharts router.

Split out of ``app/routers/rugcharts.py`` (degodding 2026-08-07) so the
router module only holds endpoint definitions. Sources: GeckoTerminal,
DexScreener, ClickHouse (chain-indexer OHLCV), Redis caching.

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

import httpx

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


# ── DexScreener trending ─────────────────────────────────────────────────


async def _fetch_dexscreener_trending(chain: str, limit: int = 20) -> tuple[list[dict[str, Any]], bool]:
    """Fetch trending tokens from DexScreener with real price data."""
    cache_key = f"rugcharts:trending:{chain}:{limit}"
    cached = await _cache_get_json(cache_key)
    if cached:
        return cached, False

    chain_map = {
        "solana": "solana",
        "ethereum": "ethereum",
        "base": "base",
        "bsc": "bsc",
        "arbitrum": "arbitrum",
        "tron": "tron",
    }
    dex_chain = chain_map.get(chain, chain)

    tokens: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://api.dexscreener.com/token-boosts/top/v1")
            if r.status_code == 200:
                data = r.json()
                items = data if isinstance(data, list) else data.get("tokens", [])
                for item in items:
                    item_chain = item.get("chainId", "")
                    if chain and item_chain != dex_chain:
                        continue
                    tokens.append(
                        {
                            "address": item.get("tokenAddress", ""),
                            "name": item.get("name", item.get("description", "")),
                            "symbol": item.get("symbol", ""),
                            "chain": item_chain,
                            "icon": item.get("icon", ""),
                            "url": item.get("url", ""),
                            "source": "boosted",
                        }
                    )
                    if len(tokens) >= limit:
                        break

            r2 = await client.get("https://api.dexscreener.com/token-profiles/latest/v1")
            if r2.status_code == 200:
                items2 = r2.json() if isinstance(r2.json(), list) else []
                for item in items2:
                    item_chain = item.get("chainId", "")
                    if chain and item_chain != dex_chain:
                        continue
                    addr = item.get("tokenAddress", "")
                    if any(t["address"] == addr for t in tokens):
                        continue
                    tokens.append(
                        {
                            "address": addr,
                            "name": item.get("name", ""),
                            "symbol": item.get("symbol", ""),
                            "chain": item_chain,
                            "icon": item.get("icon", ""),
                            "url": item.get("url", ""),
                            "source": "new_launch",
                        }
                    )
                    if len(tokens) >= limit:
                        break

            # Enrich top tokens with pair data — CONCURRENTLY (was N+1 serial).
            addresses = [t["address"] for t in tokens[:10]]
            if addresses:
                pair_results = await asyncio.gather(
                    *[_fetch_single_pair(client, dex_chain, addr) for addr in addresses],
                    return_exceptions=True,
                )
                for result in pair_results:
                    if isinstance(result, BaseException):
                        logger.debug(f"Token pair enrichment failed: {result}")
                        continue
                    addr, pair = result
                    if not pair:
                        continue
                    for t in tokens:
                        if t["address"] == addr:
                            t.update(pair)
                            break

    except httpx.TimeoutException:
        logger.warning("DexScreener trending fetch timed out after 15s")
    except httpx.HTTPStatusError as e:
        logger.warning(f"DexScreener trending returned HTTP {e.response.status_code}")
    except Exception as e:
        logger.warning(f"DexScreener trending fetch failed: {e}")

    tokens.sort(key=lambda t: t.get("volume_24h", 0), reverse=True)
    fallback_used = len(tokens) < limit

    if tokens:
        await _cache_set_json(cache_key, tokens, CACHE_TTL)

    return tokens, fallback_used


async def _fetch_single_pair(client: httpx.AsyncClient, chain: str, addr: str) -> tuple[str, dict[str, Any]]:
    """Fetch one DexScreener token's best-pair metrics (concurrency unit)."""
    r3 = await client.get(f"https://api.dexscreener.com/tokens/v1/{chain}/{addr}")
    if r3.status_code != 200:
        return addr, {}
    pairs = r3.json()
    if not isinstance(pairs, list) or not pairs:
        return addr, {}
    pair = pairs[0]
    metrics = {
        "price_usd": float(pair.get("priceUsd", 0) or 0),
        "change_24h": float((pair.get("priceChange", {}) or {}).get("h24", 0) or 0),
        "volume_24h": float((pair.get("volume", {}) or {}).get("h24", 0) or 0),
        "liquidity_usd": float((pair.get("liquidity", {}) or {}).get("usd", 0) or 0),
        "fdv": float(pair.get("fdv", 0) or 0),
        "dex": pair.get("dexId", ""),
        "pair_address": pair.get("pairAddress", ""),
        "buys_24h": ((pair.get("txns", {}) or {}).get("h24", {}) or {}).get("buys", 0),
        "sells_24h": ((pair.get("txns", {}) or {}).get("h24", {}) or {}).get("sells", 0),
        "makers": ((pair.get("txns", {}) or {}).get("h24", {}) or {}).get("makers", 0),
    }
    return addr, metrics


# ── OHLCV: GeckoTerminal ─────────────────────────────────────────────────


async def _fetch_ohlcv_gecko(
    pair_address: str, chain: str, timeframe: str = "1h", limit: int = 100
) -> list[dict[str, Any]]:
    """Fetch OHLCV candles from GeckoTerminal."""
    network = GECKO_NETWORKS.get(chain, chain)
    tf_unit, tf_aggregate = GECKO_TIMEFRAMES.get(timeframe, ("hour", 1))

    if timeframe in ("7d", "30d"):
        limit = 168 if timeframe == "7d" else 30
        tf_unit, tf_aggregate = "hour", 1

    cache_key = f"rugcharts:ohlcv:{chain}:{pair_address}:{timeframe}:{limit}"
    cached = await _cache_get_json(cache_key)
    if cached:
        return cached

    candles: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pair_address}/ohlcv/{tf_unit}"
            params: dict[str, Any] = {"aggregate": tf_aggregate, "limit": min(limit, 1000), "currency": "usd"}
            r = await client.get(url, params=params)
            if r.status_code == 200:
                ohlcv_list = r.json().get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                for c in reversed(ohlcv_list):  # chronological order
                    if len(c) >= 6:
                        candles.append(
                            {
                                "time": int(c[0]),
                                "open": float(c[1]),
                                "high": float(c[2]),
                                "low": float(c[3]),
                                "close": float(c[4]),
                                "volume": float(c[5]),
                            }
                        )
    except httpx.TimeoutException:
        logger.warning(f"GeckoTerminal OHLCV timed out after 15s for {pair_address}")
    except httpx.HTTPStatusError as e:
        logger.warning(f"GeckoTerminal OHLCV HTTP {e.response.status_code} for {pair_address}")
    except Exception as e:
        logger.warning(f"GeckoTerminal OHLCV failed for {pair_address}: {str(e)[:120]}")

    if candles:
        await _cache_set_json(cache_key, candles, OHLCV_TTL)

    return candles


# ── OHLCV: ClickHouse materialized view ──────────────────────────────────


def _timeframe_to_ch_params(timeframe: str) -> tuple[int, int]:
    """Timeframe → (interval_minutes, lookback_hours) for the hourly MV."""
    mapping = {
        "1m": (1, 6),
        "5m": (5, 24),
        "15m": (15, 48),
        "1h": (60, 168),
        "4h": (240, 672),
        "1d": (1440, 2160),
        "7d": (1440, 2160),
        "30d": (1440, 4320),
    }
    return mapping.get(timeframe, (60, 168))


async def _fetch_ohlcv_clickhouse(
    pair_address: str,
    chain: str,
    timeframe: str = "1h",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Fetch OHLCV from ``wallet_memory.dex_ohlcv_hourly`` (own indexer data).

    Sub-hourly timeframes return [] (MV is hourly) so callers fall back to
    GeckoTerminal.
    """
    cache_key = f"rugcharts:ohlcv_ch:{chain}:{pair_address}:{timeframe}:{limit}"
    cached = await _cache_get_json(cache_key)
    if cached:
        return cached

    interval_min, lookback_hours = _timeframe_to_ch_params(timeframe)
    if interval_min < 60:
        return []

    candles: list[dict[str, Any]] = []
    try:
        if interval_min == 60:
            rows = await _ch_query(
                "SELECT toUnixTimestamp(timestamp), open, high, low, close, volume "
                "FROM dex_ohlcv_hourly "
                "WHERE chain = %(chain)s AND pair_address = %(pair)s "
                "AND timestamp >= now() - INTERVAL %(lookback)s HOUR "
                "ORDER BY timestamp ASC LIMIT %(limit)s",
                {"chain": chain, "pair": pair_address, "lookback": lookback_hours, "limit": int(limit)},
            )
            candles = [
                {
                    "time": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
                for row in rows
            ]
        else:
            rows = await _ch_query(
                "SELECT toUnixTimestamp(toStartOfInterval(timestamp, INTERVAL %(interval)s MINUTE)) AS time, "
                "argMin(open, timestamp), max(high), min(low), argMax(close, timestamp), sum(volume) "
                "FROM dex_ohlcv_hourly "
                "WHERE chain = %(chain)s AND pair_address = %(pair)s "
                "AND timestamp >= now() - INTERVAL %(lookback)s HOUR "
                "GROUP BY time ORDER BY time ASC LIMIT %(limit)s",
                {
                    "chain": chain,
                    "pair": pair_address,
                    "interval": interval_min,
                    "lookback": lookback_hours,
                    "limit": int(limit),
                },
            )
            candles = [
                {
                    "time": int(row[0]),
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5]),
                }
                for row in rows
            ]
    except Exception as e:
        logger.debug(f"ClickHouse OHLCV unavailable for {pair_address}: {str(e)[:120]}")
        return []

    if candles:
        await _cache_set_json(cache_key, candles, CH_OHLCV_TTL)

    return candles


# ── Pair info: GeckoTerminal ─────────────────────────────────────────────


async def _fetch_pair_info(pair_address: str, chain: str) -> dict[str, Any]:
    """Fetch detailed pair info from GeckoTerminal."""
    network = GECKO_NETWORKS.get(chain, chain)
    cache_key = f"rugcharts:pair:{chain}:{pair_address}"
    cached = await _cache_get_json(cache_key)
    if cached:
        return cached

    info: dict[str, Any] = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pair_address}")
            if r.status_code == 200:
                data = r.json().get("data", {}).get("attributes", {})
                info = {
                    "name": data.get("name", ""),
                    "address": data.get("address", ""),
                    "symbol": (data.get("name", "") or "").split("/")[0].strip(),
                    "base_token_price_usd": float(data.get("base_token_price_usd", 0) or 0),
                    "quote_token_price_usd": float(data.get("quote_token_price_usd", 0) or 0),
                    "price_change_24h": float(data.get("price_change_percentage", {}).get("h24", 0) or 0),
                    "volume_24h": float(data.get("volume_usd", {}).get("h24", 0) or 0),
                    "volume_6h": float(data.get("volume_usd", {}).get("h6", 0) or 0),
                    "volume_1h": float(data.get("volume_usd", {}).get("h1", 0) or 0),
                    "liquidity_usd": float(data.get("reserve_in_usd", 0) or 0),
                    "fdv": float(data.get("fdv", 0) or 0),
                    "market_cap": float(data.get("market_cap_usd", 0) or 0),
                    "txns_24h_buys": int((data.get("transactions", {}) or {}).get("h24", {}).get("buys", 0) or 0),
                    "txns_24h_sells": int((data.get("transactions", {}) or {}).get("h24", {}).get("sells", 0) or 0),
                    "txns_6h_buys": int((data.get("transactions", {}) or {}).get("h6", {}).get("buys", 0) or 0),
                    "txns_6h_sells": int((data.get("transactions", {}) or {}).get("h6", {}).get("sells", 0) or 0),
                    "txns_1h_buys": int((data.get("transactions", {}) or {}).get("h1", {}).get("buys", 0) or 0),
                    "txns_1h_sells": int((data.get("transactions", {}) or {}).get("h1", {}).get("sells", 0) or 0),
                    "dex": data.get("dex_id", ""),
                    "pool_created_at": data.get("pool_created_at", ""),
                }
    except httpx.TimeoutException:
        logger.warning(f"GeckoTerminal pair info timed out after 10s for {pair_address}")
    except httpx.HTTPStatusError as e:
        logger.warning(f"GeckoTerminal pair info HTTP {e.response.status_code} for {pair_address}")
    except Exception as e:
        logger.warning(f"GeckoTerminal pair info failed for {pair_address}: {str(e)[:120]}")

    if info:
        await _cache_set_json(cache_key, info, CACHE_TTL)

    return info


# ── Scoring (pure) ───────────────────────────────────────────────────────


def _compute_volume_authenticity(pair_info: dict[str, Any], candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute volume authenticity score from available data."""
    vol_24h = pair_info.get("volume_24h", 0)
    vol_6h = pair_info.get("volume_6h", 0)
    liq = pair_info.get("liquidity_usd", 0)
    buys = pair_info.get("txns_24h_buys", 0)
    sells = pair_info.get("txns_24h_sells", 0)
    total_txns = buys + sells

    score = 100
    risk_flags: list[str] = []

    if liq > 0 and vol_24h > 0:
        ratio = vol_24h / liq
        if ratio > 100:
            score -= 30
            risk_flags.append(f"Extreme vol/liq ratio ({ratio:.0f}x) - likely wash trading")
        elif ratio > 50:
            score -= 20
            risk_flags.append(f"Very high vol/liq ratio ({ratio:.0f}x)")
        elif ratio > 20:
            score -= 10
            risk_flags.append(f"Elevated vol/liq ratio ({ratio:.0f}x)")

    if vol_24h > 0 and vol_6h > 0:
        expected_6h = vol_24h * 0.25
        if vol_6h > expected_6h * 3:
            score -= 15
            risk_flags.append("Volume concentrated in recent 6h (burst pattern)")
        elif vol_6h < expected_6h * 0.1:
            score -= 10
            risk_flags.append("Almost no recent volume (dying token)")

    if total_txns > 0:
        buy_pct = buys / total_txns
        if buy_pct < 0.2:
            score -= 15
            risk_flags.append(f"Heavy sell dominance ({sells} sells vs {buys} buys)")
        elif buy_pct > 0.9:
            score -= 10
            risk_flags.append(f"Suspiciously high buy ratio ({buy_pct * 100:.0f}%) - possible bot activity")

    if len(candles) >= 3:
        volumes = [c["volume"] for c in candles]
        avg_vol = sum(volumes) / len(volumes)
        if avg_vol > 0:
            max_vol = max(volumes)
            if max_vol > avg_vol * 10:
                score -= 10
                risk_flags.append(f"Volume spike detected ({max_vol / avg_vol:.0f}x average)")
            if len(volumes) > 5:
                std_dev = (sum((v - avg_vol) ** 2 for v in volumes) / len(volumes)) ** 0.5
                cv = std_dev / avg_vol if avg_vol > 0 else 0
                if cv < 0.05:
                    score -= 15
                    risk_flags.append("Unnaturally uniform volume distribution (bot pattern)")

    if vol_24h == 0:
        score -= 40
        risk_flags.append("No 24h trading volume")

    score = max(0, min(100, score))
    risk_level = "LOW" if score >= 70 else "MEDIUM" if score >= 40 else "HIGH"

    return {
        "authentic_score": score,
        "fake_volume_pct": max(0, 100 - score),
        "risk_level": risk_level,
        "risk_flags": risk_flags,
        "metrics": {
            "volume_24h": vol_24h,
            "liquidity_usd": liq,
            "vol_liq_ratio": round(vol_24h / liq, 2) if liq > 0 else 0,
            "buy_count": buys,
            "sell_count": sells,
            "buy_sell_ratio": round(buys / sells, 2) if sells > 0 else 0,
        },
    }


def _compute_rug_score(pair_info: dict[str, Any], vol_auth: dict[str, Any]) -> dict[str, Any]:
    """Compute overall rug risk score."""
    score = 0
    factors: list[str] = []

    liq = pair_info.get("liquidity_usd", 0)
    fdv = pair_info.get("fdv", 0)
    change = pair_info.get("price_change_24h", 0)
    buys = pair_info.get("txns_24h_buys", 0)
    sells = pair_info.get("txns_24h_sells", 0)

    if liq < 1e4:
        score += 30
        factors.append("Critically low liquidity (<$10K)")
    elif liq < 5e4:
        score += 15
        factors.append("Low liquidity (<$50K)")
    elif liq > 1e6:
        score -= 5
        factors.append("Deep liquidity pool (>$1M)")

    if fdv > 0 and liq > 0:
        ratio = fdv / liq
        if ratio > 100:
            score += 20
            factors.append(f"Extreme FDV/Liq ratio ({ratio:.0f}x)")
        elif ratio > 20:
            score += 10
            factors.append(f"Elevated FDV/Liq ratio ({ratio:.0f}x)")

    if sells > buys * 1.5 and buys > 0:
        score += 15
        factors.append(f"Heavy sell pressure ({sells} sells vs {buys} buys)")
    elif buys > sells * 1.5 and sells > 0:
        score -= 10
        factors.append("Strong organic buy pressure")

    if change < -20:
        score += 20
        factors.append(f"Massive dump ({change:.1f}%)")
    elif change > 50:
        score += 8
        factors.append(f"Extreme pump (+{change:.1f}%) - potential exit liquidity")
    elif change > 10:
        score += 5
        factors.append("Rapid price increase")

    auth_score = vol_auth.get("authentic_score", 100)
    if auth_score < 50:
        score += 15
        factors.append(f"Low volume authenticity ({auth_score}/100)")

    created = pair_info.get("pool_created_at", "")
    if created:
        try:
            from datetime import datetime

            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_hours = (datetime.now(created_dt.tzinfo) - created_dt).total_seconds() / 3600
            if age_hours < 1:
                score += 15
                factors.append(f"Brand new pool ({age_hours:.1f}h old)")
            elif age_hours < 24:
                score += 8
                factors.append("New pool (<24h old)")
        except (ValueError, TypeError) as e:
            logger.debug(f"Pool age parse failed: {e}")

    score = max(0, min(100, score))
    level = "SAFE" if score < 30 else "CAUTION" if score < 60 else "DANGER"
    color = "#10b981" if score < 30 else "#f59e0b" if score < 60 else "#ef4444"

    return {"score": score, "level": level, "color": color, "factors": factors}


# ── Holder stats (own indexer first; provider ladder, never single-source) ──

HELIUS_KEY = os.getenv("HELIUS_API_KEY", "")
INTERNAL_API_BASE = os.getenv("RMI_INTERNAL_API_BASE", "http://localhost:8000")


async def _holder_stats_from_clickhouse(token_address: str, chain: str) -> dict[str, Any] | None:
    """Top-holder concentration from OUR indexer (wallet_memory.holder_balances).

    Works for every EVM chain the lite-chain-indexer covers (base, bsc,
    ethereum, polygon, arbitrum, optimism, avalanche, gnosis, tron, ...).
    Percentages are computed on raw integer balances so no decimals lookup
    is needed. Supply denominator = sum of indexed balances — an honest
    transfer-derived approximation, labeled as such.
    """
    token_l = (token_address or "").lower()
    if not token_l or not chain:
        return None
    try:
        top_rows = await _ch_query(
            "SELECT address, balance FROM holder_balances "
            "WHERE chain = %(chain)s AND lower(token_address) = %(token)s "
            "ORDER BY balance DESC LIMIT 20",
            {"chain": chain, "token": token_l},
        )
        if not top_rows:
            return None
        total_rows = await _ch_query(
            "SELECT sum(balance), count() FROM holder_balances "
            "WHERE chain = %(chain)s AND lower(token_address) = %(token)s",
            {"chain": chain, "token": token_l},
        )
        total_supply = float(total_rows[0][0]) if total_rows and total_rows[0][0] else 0.0
        n_accounts = int(total_rows[0][1]) if total_rows and total_rows[0][1] else 0
        if total_supply <= 0:
            return None
        balances = sorted((float(b) for _a, b in top_rows), reverse=True)
        return {
            "source": "chain_indexer.clickhouse",
            "top_holder_pct": round(balances[0] / total_supply * 100, 2),
            "top10_holder_pct": round(sum(balances[:10]) / total_supply * 100, 2),
            "sampled_accounts": n_accounts,
            "metric": "indexed_transfer_balances",
        }
    except Exception as e:
        logger.debug(f"CH holder stats failed {chain}/{token_address}: {e}")
        return None


async def _holder_stats_from_solana_transfers(token_address: str) -> dict[str, Any] | None:
    """Solana holder concentration from OUR spl balance-change index.

    Coverage is limited to mints the indexer has tracked so far; returns
    None quickly when the mint is unknown so callers fall through.
    """
    mint = (token_address or "").lower()
    if not mint:
        return None
    try:
        rows = await _ch_query(
            "SELECT owner, argMax(post_amount, slot) AS bal "
            "FROM solana_spl_balance_changes WHERE lower(mint) = %(mint)s "
            "AND owner != '' GROUP BY owner ORDER BY bal DESC LIMIT 20",
            {"mint": mint},
        )
        if len(rows) < 2:
            return None
        balances = []
        for _owner, bal in rows:
            try:
                balances.append(float(bal))
            except (TypeError, ValueError):
                continue
        balances = [b for b in balances if b > 0]
        total = sum(balances)
        if total <= 0:
            return None
        return {
            "source": "chain_indexer.solana_spl",
            "top_holder_pct": round(balances[0] / total * 100, 2),
            "top10_holder_pct": round(sum(balances[:10]) / total * 100, 2),
            "sampled_accounts": len(balances),
            "metric": "indexed_balance_changes",
        }
    except Exception as e:
        logger.debug(f"SPL holder stats failed {token_address}: {e}")
        return None


async def _holder_stats_from_scanner_api(chain: str, token_address: str) -> dict[str, Any] | None:
    """Fallback: our own multi-provider scanner service.

    Called over HTTP (not imported) so this package stays decoupled from
    backend internals — a standalone rugcharts deployment consumes the
    exact same surface by pointing RMI_INTERNAL_API_BASE at the platform.
    """
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{INTERNAL_API_BASE}/api/v2/scanner/token/holders/{chain}/{token_address}")
            if r.status_code != 200:
                return None
            d = r.json().get("data", {}) or {}
            if d.get("top_holder_pct") is None and d.get("top10_holders_pct") is None:
                return None
            return {
                "source": f"scanner_service.{d.get('provider', 'aggregated')}",
                "top_holder_pct": float(d.get("top_holder_pct") or 0),
                "top10_holder_pct": float(d.get("top10_holders_pct") or 0),
                "sampled_accounts": int(d.get("holder_count") or 0),
                "metric": "scanner_aggregated",
            }
    except Exception as e:
        logger.debug(f"Scanner holder fallback failed {chain}/{token_address}: {e}")
        return None


async def _holder_stats_from_helius(token_address: str) -> dict[str, Any] | None:
    """LAST-resort tier (Solana only): Helius getTokenLargestAccounts."""
    if not HELIUS_KEY or not token_address:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                f"https://mainnet.helius-rpc.com/?api-key={HELIUS_KEY}",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getTokenLargestAccounts",
                    "params": [token_address],
                },
            )
            if r.status_code != 200:
                return None
            accounts = r.json().get("result", {}).get("value", [])
            supplies = [float(a.get("uiAmount") or 0) for a in accounts if a.get("uiAmount") is not None]
            total = sum(supplies)
            if total <= 0:
                return None
            top20 = sorted(supplies, reverse=True)[:20]
            return {
                "source": "helius",
                "top_holder_pct": round(top20[0] / total * 100, 2),
                "top10_holder_pct": round(sum(top20[:10]) / total * 100, 2),
                "sampled_accounts": len(accounts),
                "metric": "largest_accounts_concentration",
            }
    except Exception as e:
        logger.debug(f"Helius holder stats failed for {token_address}: {e}")
        return None


async def _fetch_holder_stats(token_address: str, chain: str) -> dict[str, Any] | None:
    """Holder concentration via a provider LADDER — own indexer first.

    Tier 1: ClickHouse holder_balances (all indexed EVM chains)
    Tier 2: ClickHouse solana SPL balance changes (our Solana coverage)
    Tier 3: internal multi-provider scanner service
    Tier 4: Helius (Solana only, last resort)

    Returns None ONLY when every tier fails — unknown stays unknown, never 0.
    """
    if not token_address or not chain:
        return None

    cache_key = f"rugcharts:holders:{chain}:{(token_address or '').lower()}"
    cached = await _cache_get_json(cache_key)
    if cached is not None:
        return cached

    stats: dict[str, Any] | None = None
    if chain == "solana":
        stats = (
            await _holder_stats_from_solana_transfers(token_address)
            or await _holder_stats_from_scanner_api(chain, token_address)
            or await _holder_stats_from_helius(token_address)
        )
    else:
        stats = await _holder_stats_from_clickhouse(token_address, chain) or (
            await _holder_stats_from_scanner_api(chain, token_address)
        )

    if stats:
        await _cache_set_json(cache_key, stats, CACHE_TTL)
    return stats


# ── Live trade tape (ClickHouse dex_events, unfiltered by whale flag) ────

RECENT_TRADES_TTL = 5  # seconds — tape freshness window


async def _fetch_recent_trades_clickhouse(
    token_address: str,
    chain: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Latest dex swaps touching a token — feeds /ws/trades live tape.

    Unlike the whale query this has NO whale/min_usd filter: the tape shows
    every trade, labeled by classifier.py. Short TTL (5s) keeps polling
    viewers fresh without hammering ClickHouse.
    """
    token_l = (token_address or "").lower()
    if not token_l or not chain:
        return []

    cache_key = f"rugcharts:tape:{chain}:{token_l}:{limit}"
    cached = await _cache_get_json(cache_key)
    if cached:
        return cached

    sql = (
        "SELECT toUnixTimestamp(block_timestamp), price_usd, volume_usd, "
        "tx_hash, token0, token1, recipient, "
        "amount0_in, amount1_in, amount0_out, amount1_out "
        "FROM dex_events "
        "WHERE chain = %(chain)s "
        "AND (lower(token0) = %(token)s OR lower(token1) = %(token)s) "
        "ORDER BY block_timestamp DESC LIMIT %(limit)s"
    )
    try:
        rows = await _ch_query(sql, {"chain": chain, "token": token_l, "limit": int(limit)})
    except Exception as e:
        logger.debug(f"recent_trades_query_failed: {e}")
        return []

    trades: list[dict[str, Any]] = []
    for row in rows:
        trades.append(
            {
                "time": int(row[0]),
                "price_usd": float(row[1] or 0),
                "volume_usd": float(row[2] or 0),
                "tx_hash": row[3] or "",
                "token0": row[4] or "",
                "token1": row[5] or "",
                "wallet": row[6] or "",
                "side": _whale_side(token_l, row[4] or "", row[5] or "", row[7], row[8], row[9], row[10]),
                "chain": chain,
            }
        )

    if trades:
        await _cache_set_json(cache_key, trades, RECENT_TRADES_TTL)

    return trades


# ── Whale trades (ClickHouse dex_events) ─────────────────────────────────


def _whale_side(
    token: str,
    token0: str,
    token1: str,
    amount0_in: Any,
    amount1_in: Any,
    amount0_out: Any,
    amount1_out: Any,
) -> str:
    """Derive a whale trade's side for a target token.

    Uniswap V2 Swap semantics: exactly one leg flows IN and the other OUT.
      - a0_out > 0 → token0 bought → token0: BUY, token1: SELL
      - a1_out > 0 → token1 bought → token1: BUY, token0: SELL
    V3 swaps store absolute amounts (no out-flow) → side unknowable.
    """
    try:
        a0_out = float(amount0_out or 0)
        a1_out = float(amount1_out or 0)
    except (TypeError, ValueError):
        return "unknown"

    token_l = (token or "").lower()
    token0_l = (token0 or "").lower()
    token1_l = (token1 or "").lower()

    if a0_out > 0:
        return "buy" if token_l == token0_l else "sell" if token_l == token1_l else "unknown"
    if a1_out > 0:
        return "buy" if token_l == token1_l else "sell" if token_l == token0_l else "unknown"
    return "unknown"


async def _fetch_whale_trades_clickhouse(
    token_address: str,
    chain: str,
    limit: int = 100,
    min_usd: float = WHALE_MIN_USD,
) -> list[dict[str, Any]]:
    """Fetch large DEX swaps for a token from chain-indexer ClickHouse.

    Queries ``wallet_memory.dex_events`` for events where the token is on
    either side of the pair and USD notional >= min_usd. Each marker carries
    ``wallet`` (taker proxy = output receiver — heuristic attribution, see
    classifier.py) alongside time/price/side/tx_hash.
    """
    token_l = (token_address or "").lower()
    if not token_l or not chain:
        return []

    cache_key = f"rugcharts:whales:{chain}:{token_l}:{limit}:{min_usd}"
    cached = await _cache_get_json(cache_key)
    if cached:
        return cached

    sql_base = (
        "SELECT toUnixTimestamp(block_timestamp), price_usd, volume_usd, "
        "tx_hash, token0, token1, recipient, "
        "amount0_in, amount1_in, amount0_out, amount1_out "
        "FROM dex_events "
        "WHERE chain = %(chain)s AND whale = 1 "
    )
    sql = sql_base + (
        "AND volume_usd >= %(min_usd)s "
        "AND (lower(token0) = %(token)s OR lower(token1) = %(token)s) "
        "ORDER BY block_timestamp DESC LIMIT %(limit)s"
        if min_usd > 0
        else "AND (lower(token0) = %(token)s OR lower(token1) = %(token)s) "
        "ORDER BY block_timestamp DESC LIMIT %(limit)s"
    )
    params: dict[str, Any] = {"chain": chain, "token": token_l, "limit": int(limit)}
    if min_usd > 0:
        params["min_usd"] = float(min_usd)

    try:
        rows = await _ch_query(sql, params)
    except Exception as e:
        logger.debug(f"whale_trades_query_failed: {e}")
        return []

    trades: list[dict[str, Any]] = []
    for row in rows:
        trades.append(
            {
                "time": int(row[0]),
                "price_usd": float(row[1] or 0),
                "volume_usd": float(row[2] or 0),
                "tx_hash": row[3] or "",
                "token0": row[4] or "",
                "token1": row[5] or "",
                "wallet": row[6] or "",
                "side": _whale_side(token_l, row[4] or "", row[5] or "", row[7], row[8], row[9], row[10]),
                "chain": chain,
            }
        )

    if trades:
        await _cache_set_json(cache_key, trades, WHALE_TTL)

    return trades


# ── Chain data freshness (ops visibility — audit WIN-9) ──────────────────


async def _chain_freshness() -> dict[str, dict[str, Any]]:
    """Per-chain freshness of our own indexer tables (dex_events).

    Makes coverage gaps like "no solana rows" or "bsc stale since Aug 9"
    visible to ops instead of silently returning empty overlays.
    """
    try:
        rows = await _ch_query(
            "SELECT chain, count(), max(block_timestamp), "
            "dateDiff('hour', max(block_timestamp), now()) "
            "FROM dex_events GROUP BY chain ORDER BY max(block_timestamp) DESC",
            {},
        )
    except Exception as e:
        logger.debug(f"freshness query failed: {e}")
        return {}

    out: dict[str, dict[str, Any]] = {}
    for chain, cnt, latest, hours in rows:
        hours = int(hours or 0)
        out[chain] = {
            "events": int(cnt),
            "latest_utc": str(latest),
            "hours_stale": hours,
            "status": "fresh" if hours <= 2 else "delayed" if hours <= 24 else "stale",
        }
    return out
