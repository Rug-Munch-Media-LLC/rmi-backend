"""OHLCV + market data fetching for RugCharts (GeckoTerminal, DexScreener, ClickHouse)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .common import (
    CACHE_TTL,
    CH_OHLCV_TTL,
    GECKO_NETWORKS,
    GECKO_TIMEFRAMES,
    OHLCV_TTL,
    _cache_get_json,
    _cache_set_json,
    _ch_query,
    logger,
)


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
