"""Data-fetching + scoring helpers for the RugCharts router.

Split out of ``app/routers/rugcharts.py`` (degodding 2026-08-07) so the
router module only holds endpoint definitions. Sources: GeckoTerminal,
DexScreener, ClickHouse (chain-indexer OHLCV), Redis caching.
"""

from __future__ import annotations

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
    except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError, json.JSONDecodeError) as e:
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
                    # Filter by chain if specified
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

            # Also get new pairs for the chain (high volume new launches)
            r2 = await client.get("https://api.dexscreener.com/token-profiles/latest/v1")
            if r2.status_code == 200:
                data2 = r2.json()
                items2 = data2 if isinstance(data2, list) else []
                for item in items2:
                    item_chain = item.get("chainId", "")
                    if chain and item_chain != dex_chain:
                        continue
                    # Check if already in list
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

            # Enrich with pair data for volume/price
            if tokens:
                addresses = [t["address"] for t in tokens[:10]]
                for addr in addresses:
                    try:
                        r3 = await client.get(f"https://api.dexscreener.com/tokens/v1/{dex_chain}/{addr}")
                        if r3.status_code == 200:
                            pairs = r3.json()
                            if isinstance(pairs, list) and pairs:
                                pair = pairs[0]  # Best pair
                                for t in tokens:
                                    if t["address"] == addr:
                                        t["price_usd"] = float(pair.get("priceUsd", 0) or 0)
                                        t["change_24h"] = float((pair.get("priceChange", {}) or {}).get("h24", 0) or 0)
                                        t["volume_24h"] = float((pair.get("volume", {}) or {}).get("h24", 0) or 0)
                                        t["liquidity_usd"] = float((pair.get("liquidity", {}) or {}).get("usd", 0) or 0)
                                        t["fdv"] = float(pair.get("fdv", 0) or 0)
                                        t["dex"] = pair.get("dexId", "")
                                        t["pair_address"] = pair.get("pairAddress", "")
                                        t["buys_24h"] = (pair.get("txns", {}) or {}).get("h24", {}).get("buys", 0)
                                        t["sells_24h"] = (pair.get("txns", {}) or {}).get("h24", {}).get("sells", 0)
                                        t["makers"] = (pair.get("txns", {}) or {}).get("h24", {}).get("makers", 0)
                                        break
                    except (httpx.HTTPError, TimeoutError, json.JSONDecodeError) as e:
                        logger.debug(f"Token pair enrichment failed for {addr}: {e}")

    except httpx.TimeoutException:
        logger.warning("DexScreener trending fetch timed out after 15s")
    except httpx.HTTPStatusError as e:
        logger.warning(f"DexScreener trending returned HTTP {e.response.status_code}")
    except Exception as e:
        logger.warning(f"DexScreener trending fetch failed: {e}")

    # Sort by volume
    tokens.sort(key=lambda t: t.get("volume_24h", 0), reverse=True)

    fallback_used = len(tokens) < limit  # Fewer results than requested = some API fallback

    if tokens:
        try:
            r = _r()
            r.setex(cache_key, CACHE_TTL, json.dumps(tokens))
            r.close()
        except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError) as e:
            logger.debug(f"Redis cache write failed for {cache_key}: {e}")

    return tokens, fallback_used


async def _fetch_ohlcv_gecko(pair_address: str, chain: str, timeframe: str = "1h", limit: int = 100) -> list[dict]:
    """Fetch OHLCV candles from GeckoTerminal."""
    network = GECKO_NETWORKS.get(chain, chain)
    tf_info = GECKO_TIMEFRAMES.get(timeframe, ("hour", 1))
    tf_aggregate = tf_info[1]
    tf_unit = tf_info[0]

    # For longer timeframes, adjust limit
    if timeframe in ("7d", "30d"):
        limit = 168 if timeframe == "7d" else 30
        tf_unit = "hour" if timeframe == "7d" else "day"
        tf_aggregate = 1

    cache_key = f"rugcharts:ohlcv:{chain}:{pair_address}:{timeframe}:{limit}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError, json.JSONDecodeError) as e:
        logger.debug(f"Redis OHLCV cache read failed: {e}")

    candles = []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            url = f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pair_address}/ohlcv/{tf_unit}"
            params = {"aggregate": tf_aggregate, "limit": min(limit, 1000), "currency": "usd"}
            r = await client.get(url, params=params)
            if r.status_code == 200:
                data = r.json()
                ohlcv_list = data.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
                for c in reversed(ohlcv_list):  # Reverse to chronological order
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
        try:
            r = _r()
            r.setex(cache_key, OHLCV_TTL, json.dumps(candles))
            r.close()
        except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError) as e:
            logger.debug(f"Redis OHLCV cache write failed: {e}")

    return candles


def _timeframe_to_ch_params(timeframe: str) -> tuple[int, int]:
    """Convert rugcharts timeframe to (interval_minutes, lookback_hours).

    Used by the ClickHouse OHLCV path. The materialized view is hourly, so
    sub-hourly timeframes return an interval below 60 (handled by falling
    back to GeckoTerminal in the caller).
    """
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
) -> list[dict]:
    """Fetch OHLCV candles from the chain-indexer ClickHouse materialized view.

    Queries ``wallet_memory.dex_ohlcv_hourly`` (pre-computed 1h candles from
    raw swap events). For sub-hourly timeframes returns an empty list so the
    caller can fall back to GeckoTerminal. Uses the native ``clickhouse_driver``
    (port 9000) — the same client the scanner own-data engines use.
    """
    cache_key = f"rugcharts:ohlcv_ch:{chain}:{pair_address}:{timeframe}:{limit}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError, json.JSONDecodeError) as e:
        logger.debug(f"Redis CH OHLCV cache read failed: {e}")

    interval_min, lookback_hours = _timeframe_to_ch_params(timeframe)
    # Sub-hourly not available from the hourly materialized view — fall back.
    if interval_min < 60:
        return []

    try:
        from clickhouse_driver import Client as CHClient
    except ImportError:
        logger.debug("clickhouse_driver not installed — CH OHLCV unavailable")
        return []

    candles: list[dict] = []
    try:
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
            if interval_min == 60:
                rows = client.execute(
                    "SELECT toUnixTimestamp(timestamp), open, high, low, close, volume, trades "
                    "FROM dex_ohlcv_hourly "
                    "WHERE chain = %(chain)s AND pair_address = %(pair)s "
                    "AND timestamp >= now() - INTERVAL %(lookback)s HOUR "
                    "ORDER BY timestamp ASC LIMIT %(limit)s",
                    {
                        "chain": chain,
                        "pair": pair_address,
                        "lookback": lookback_hours,
                        "limit": int(limit),
                    },
                )
                for row in rows:
                    candles.append(
                        {
                            "time": int(row[0]),
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                        }
                    )
            else:
                # Resample hourly → 4h/1d/7d/30d via toStartOfInterval
                rows = client.execute(
                    "SELECT toUnixTimestamp(toStartOfInterval(timestamp, INTERVAL %(interval)s MINUTE)) AS time, "
                    "argMin(open, timestamp), max(high), min(low), argMax(close, timestamp), sum(volume), sum(trades) "
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
                for row in rows:
                    candles.append(
                        {
                            "time": int(row[0]),
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                        }
                    )
        finally:
            client.disconnect()
    except (ImportError, ConnectionError, TimeoutError, OSError, RuntimeError) as e:
        logger.debug(f"ClickHouse OHLCV unavailable for {pair_address}: {str(e)[:120]}")

    if candles:
        try:
            r = _r()
            r.setex(cache_key, CH_OHLCV_TTL, json.dumps(candles))
            r.close()
        except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError) as e:
            logger.debug(f"Redis CH OHLCV cache write failed: {e}")

    return candles


async def _fetch_pair_info(pair_address: str, chain: str) -> dict:
    """Fetch detailed pair info from GeckoTerminal."""
    network = GECKO_NETWORKS.get(chain, chain)
    cache_key = f"rugcharts:pair:{chain}:{pair_address}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError, json.JSONDecodeError) as e:
        logger.debug(f"Redis pair cache read failed: {e}")

    info = {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pair_address}")
            if r.status_code == 200:
                data = r.json().get("data", {}).get("attributes", {})
                info = {
                    "name": data.get("name", ""),
                    "address": data.get("address", ""),
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
        try:
            r = _r()
            r.setex(cache_key, CACHE_TTL, json.dumps(info))
            r.close()
        except (ConnectionError, TimeoutError, OSError, redis.exceptions.ResponseError) as e:
            logger.debug(f"Redis pair cache write failed: {e}")

    return info


def _compute_volume_authenticity(pair_info: dict, candles: list[dict]) -> dict:
    """Compute volume authenticity score from available data."""
    vol_24h = pair_info.get("volume_24h", 0)
    vol_6h = pair_info.get("volume_6h", 0)
    pair_info.get("volume_1h", 0)
    liq = pair_info.get("liquidity_usd", 0)
    buys = pair_info.get("txns_24h_buys", 0)
    sells = pair_info.get("txns_24h_sells", 0)
    total_txns = buys + sells

    score = 100
    risk_flags = []

    # 1. Volume/Liquidity ratio check
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

    # 2. Volume distribution across timeframes
    if vol_24h > 0 and vol_6h > 0:
        expected_6h = vol_24h * 0.25  # 6h should be ~25% of 24h
        if vol_6h > expected_6h * 3:
            score -= 15
            risk_flags.append("Volume concentrated in recent 6h (burst pattern)")
        elif vol_6h < expected_6h * 0.1:
            score -= 10
            risk_flags.append("Almost no recent volume (dying token)")

    # 3. Buy/sell ratio
    if total_txns > 0:
        buy_pct = buys / total_txns
        if buy_pct < 0.2:
            score -= 15
            risk_flags.append(f"Heavy sell dominance ({sells} sells vs {buys} buys)")
        elif buy_pct > 0.9:
            score -= 10
            risk_flags.append(f"Suspiciously high buy ratio ({buy_pct * 100:.0f}%) - possible bot activity")

    # 4. Candle analysis (if available)
    if len(candles) >= 3:
        volumes = [c["volume"] for c in candles]
        avg_vol = sum(volumes) / len(volumes)
        if avg_vol > 0:
            # Check for volume spikes (single candle >> average)
            max_vol = max(volumes)
            if max_vol > avg_vol * 10:
                score -= 10
                risk_flags.append(f"Volume spike detected ({max_vol / avg_vol:.0f}x average)")
            # Check for uniform volume (bot-like)
            if len(volumes) > 5:
                std_dev = (sum((v - avg_vol) ** 2 for v in volumes) / len(volumes)) ** 0.5
                cv = std_dev / avg_vol if avg_vol > 0 else 0
                if cv < 0.05:
                    score -= 15
                    risk_flags.append("Unnaturally uniform volume distribution (bot pattern)")

    # 5. Zero volume check
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


def _compute_rug_score(pair_info: dict, vol_auth: dict) -> dict:
    """Compute overall rug risk score."""
    score = 0
    factors = []

    liq = pair_info.get("liquidity_usd", 0)
    fdv = pair_info.get("fdv", 0)
    pair_info.get("volume_24h", 0)
    change = pair_info.get("price_change_24h", 0)
    buys = pair_info.get("txns_24h_buys", 0)
    sells = pair_info.get("txns_24h_sells", 0)

    # Liquidity risk
    if liq < 1e4:
        score += 30
        factors.append("Critically low liquidity (<$10K)")
    elif liq < 5e4:
        score += 15
        factors.append("Low liquidity (<$50K)")
    elif liq > 1e6:
        score -= 5
        factors.append("Deep liquidity pool (>$1M)")

    # FDV/Liquidity ratio
    if fdv > 0 and liq > 0:
        ratio = fdv / liq
        if ratio > 100:
            score += 20
            factors.append(f"Extreme FDV/Liq ratio ({ratio:.0f}x)")
        elif ratio > 20:
            score += 10
            factors.append(f"Elevated FDV/Liq ratio ({ratio:.0f}x)")

    # Sell pressure
    if sells > buys * 1.5 and buys > 0:
        score += 15
        factors.append(f"Heavy sell pressure ({sells} sells vs {buys} buys)")
    elif buys > sells * 1.5 and sells > 0:
        score -= 10
        factors.append("Strong organic buy pressure")

    # Price action
    if change < -20:
        score += 20
        factors.append(f"Massive dump ({change:.1f}%)")
    elif change > 50:
        score += 8
        factors.append(f"Extreme pump (+{change:.1f}%) - potential exit liquidity")
    elif change > 10:
        score += 5
        factors.append("Rapid price increase")

    # Volume authenticity penalty
    auth_score = vol_auth.get("authentic_score", 100)
    if auth_score < 50:
        score += 15
        factors.append(f"Low volume authenticity ({auth_score}/100)")

    # Pool age
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
