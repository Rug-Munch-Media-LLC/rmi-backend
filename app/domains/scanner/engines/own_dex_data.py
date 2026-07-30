"""
Own DEX Data — ClickHouse-Powered DexScreener Replacement
============================================================
Phase 1 of the Data Ownership Strategy.

Eliminates 111 DexScreener API calls per token scan by querying
chain-indexer ClickHouse dex_events (47M+ DEX swap events).

Architecture:
  - Provides the EXACT same output schema as DexScreener API
    so volume_analysis.py and market.py can drop in replacement
  - Falls back to real DexScreener API if ClickHouse returns < 3 swaps
  - Caches results per token for the lifetime of a scan process
  - All queries run asynchronously via asyncio.to_thread

Usage:
    from app.domains.scanner.engines.own_dex_data import get_own_dex_data

    result = await get_own_dex_data("0x...", "ethereum")
    if result["source"] == "own":
        # Use our data
        pairs = result["pairs"]
        volume = sum(p["volume"]["h24"] for p in pairs)
    elif result["source"] == "fallback":
        # DexScreener API was used as fallback
        logger.warning("DexScreener fallback used for %s", token)

Data Source:
  Table: wallet_memory.dex_events  (47M+ rows)
  Schema:
    type            String  — swap, mint, burn
    chain           String  — ethereum, bsc, polygon, etc.
    block_number    UInt64
    block_timestamp DateTime
    tx_hash         String
    log_index       UInt32
    contract_address String — pool address
    dex             String  — uniswap_v2, pancakeswap_v2, etc.
    version         String  — v2, v3
    token0          String  — base token address
    token1          String  — quote token address
    sender          String
    recipient       String
    amount0_in      Nullable(String) — token0 amount coming IN
    amount1_in      Nullable(String) — token1 amount coming IN
    amount0_out     Nullable(String) — token0 amount going OUT
    amount1_out     Nullable(String) — token1 amount going OUT
    amount0         Nullable(String) — for mint/burn events
    amount1         Nullable(String) — for mint/burn events
    whale           UInt8   — flagged as large transaction
"""

from __future__ import annotations

import os
import time
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── DexScreener-compatible response cache ─────────────────────────────────
_DEX_CACHE: dict[str, dict | None] = {}
_DEX_CACHE_MAX = 100

# ── Lazy ClickHouse client (reuses chain_indexer.py pattern) ─────────────
_ch_client: Any = None


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client for wallet_memory database."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT", "9000"))
    ch_user = os.getenv("CLICKHOUSE_USER") or os.getenv("CH_USER", "rmi")
    ch_pass = (os.getenv("CLICKHOUSE_PASSWORD") or os.getenv("CH_PASSWORD")) or ""
    ch_db = "wallet_memory"

    try:
        from clickhouse_driver import Client as CHClient

        _ch_client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=3,
            send_receive_timeout=15,
        )
        _ch_client.execute("SELECT 1")
        logger.info("Own DEX data ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError) as e:
        logger.warning("Own DEX data ClickHouse unavailable: %s", e)
        _ch_client = None
        return None


async def _run_sync(fn, *args, **kwargs):
    """Run a sync function in a thread pool (clickhouse_driver is sync)."""
    import asyncio

    return await asyncio.to_thread(fn, *args, **kwargs)


# ── Chain name normalization ─────────────────────────────────────────────
_CHAIN_MAP: dict[str, str] = {
    "ethereum": "ethereum",
    "eth": "ethereum",
    "mainnet": "ethereum",
    "bsc": "bsc",
    "bnb": "bsc",
    "binance": "bsc",
    "polygon": "polygon",
    "matic": "polygon",
    "arbitrum": "arbitrum",
    "arb": "arbitrum",
    "optimism": "optimism",
    "op": "optimism",
    "base": "base",
    "avalanche": "avalanche",
    "avax": "avalanche",
    "fantom": "fantom",
    "ftm": "fantom",
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "solana": "solana",
}


def _normalize_chain(chain: str) -> str:
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_own_dex_data(
    token_address: str,
    chain: str,
    hours_24: int = 24,
) -> dict[str, Any]:
    """Get DEX trading data from ClickHouse — Drop-in DexScreener replacement.

    Returns a DexScreener-compatible dict structure:
    {
        "pairs": [
            {
                "chainId": "ethereum",
                "dexId": "uniswap_v2",
                "pairAddress": "0x...",
                "priceUsd": "0.001234",
                "volume": {"h24": 12345.67},
                "liquidity": {"usd": 50000.0},
                "txns": {"h24": {"buys": 42, "sells": 38}},
                "priceChange": {"h24": 5.2},
                "fdv": 1000000.0,
            }
        ],
        "source": "own",  # or "fallback" if DexScreener was used
        "metrics": {
            "total_volume_24h": float,
            "total_liquidity_usd": float,
            "swap_count_24h": int,
            "unique_pools": int,
        }
    }

    Args:
        token_address: Token contract address
        chain: Chain name (ethereum, bsc, polygon, etc.)
        hours_24: Lookback window in hours

    Returns:
        Dict with pairs array matching DexScreener schema
    """
    _start = time.monotonic()

    # Check process-level cache first
    cache_key = f"{token_address.lower()}:{chain}:{hours_24}"
    if cache_key in _DEX_CACHE:
        cached = _DEX_CACHE[cache_key]
        if cached:
            return cached

    token_address = token_address.lower()
    chain = _normalize_chain(chain)

    # Query ClickHouse for dex_events
    raw_swaps = await _query_dex_swaps(token_address, chain, hours_24)

    # If insufficient data, fall back to DexScreener API
    if len(raw_swaps) < 3:
        fallback = await _fallback_to_dexscreener(token_address)
        if fallback:
            # Cache and return
            _DEX_CACHE[cache_key] = fallback
            if len(_DEX_CACHE) > _DEX_CACHE_MAX:
                _DEX_CACHE.pop(next(iter(_DEX_CACHE)), None)
            _elapsed_ms = (time.monotonic() - _start) * 1000
            logger.info(
                "own_dex_data_latency",
                ms=round(_elapsed_ms, 1),
                swaps=len(fallback.get("pairs", [])),
                chain=chain,
                source="fallback",
            )
            return fallback
        _elapsed_ms = (time.monotonic() - _start) * 1000
        logger.info(
            "own_dex_data_latency",
            ms=round(_elapsed_ms, 1),
            swaps=0,
            chain=chain,
            source="unavailable",
        )
        return {
            "pairs": [],
            "source": "unavailable",
            "metrics": {"total_volume_24h": 0, "total_liquidity_usd": 0, "swap_count_24h": 0, "unique_pools": 0},
            "error": "No DEX data available from ClickHouse or DexScreener",
        }

    # Build DexScreener-compatible pairs from our data
    pairs = _build_pairs(raw_swaps, token_address, chain)

    # Compute aggregate metrics
    total_volume = sum(p["volume"]["h24"] for p in pairs)
    total_liquidity = sum(p["liquidity"]["usd"] for p in pairs)
    total_swaps = sum(p["txns"]["h24"]["buys"] + p["txns"]["h24"]["sells"] for p in pairs)
    unique_pools = len({p["pairAddress"] for p in pairs})

    result: dict[str, Any] = {
        "pairs": pairs,
        "source": "own",
        "metrics": {
            "total_volume_24h": round(total_volume, 2),
            "total_liquidity_usd": round(total_liquidity, 2),
            "swap_count_24h": total_swaps,
            "unique_pools": unique_pools,
        },
    }

    # Cache (with LRU eviction)
    _DEX_CACHE[cache_key] = result
    if len(_DEX_CACHE) > _DEX_CACHE_MAX:
        _DEX_CACHE.pop(next(iter(_DEX_CACHE)), None)

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "own_dex_data_latency",
        ms=round(_elapsed_ms, 1),
        swaps=len(pairs),
        chain=chain,
        source="own",
    )
    return result


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES
# ═══════════════════════════════════════════════════════════════════════════


async def _query_dex_swaps(
    token_address: str,
    chain: str,
    hours_back: int = 24,
) -> list[dict[str, Any]]:
    """Query dex_events for swap events involving this token.

    Returns list of raw swap dicts ordered by block_timestamp DESC.
    """

    def _query() -> list[tuple]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "addr0": token_address,
            "addr1": token_address,
            "chain": chain,
            "hours": hours_back,
        }

        try:
            return client.execute(
                """
                SELECT
                    contract_address, dex, version,
                    token0, token1,
                    amount0_in, amount1_in, amount0_out, amount1_out,
                    amount0, amount1,
                    block_timestamp, tx_hash, log_index,
                    type, sender, recipient, whale
                FROM wallet_memory.dex_events
                WHERE chain = %(chain)s
                  AND (token0 = %(addr0)s OR token1 = %(addr1)s)
                  AND type = 'swap'
                  AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
                ORDER BY block_timestamp DESC
                LIMIT 500
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("dex_events query failed for %s: %s", token_address, exc)
            return []

    rows = await _run_sync(_query)
    if not rows:
        return []

    swaps = []
    for row in rows:
        try:
            swaps.append(
                {
                    "contract_address": row[0],
                    "dex": row[1],
                    "version": row[2],
                    "token0": row[3],
                    "token1": row[4],
                    "amount0_in": _parse_decimal(row[5]),
                    "amount1_in": _parse_decimal(row[6]),
                    "amount0_out": _parse_decimal(row[7]),
                    "amount1_out": _parse_decimal(row[8]),
                    "amount0": _parse_decimal(row[9]),
                    "amount1": _parse_decimal(row[10]),
                    "block_timestamp": row[11].isoformat() if hasattr(row[11], "isoformat") else str(row[11]),
                    "tx_hash": row[12],
                    "log_index": row[13],
                    "type": row[14],
                    "sender": row[15],
                    "recipient": row[16],
                    "whale": bool(row[17]) if len(row) > 17 else False,
                    "timestamp": row[11].timestamp() if hasattr(row[11], "timestamp") else time.time(),
                }
            )
        except (ValueError, TypeError, IndexError) as e:
            logger.debug("Failed to parse dex_events row: %s", e)
            continue

    return swaps


async def _query_pool_stats(
    pool_address: str,
    chain: str,
    hours_back: int = 24,
) -> dict[str, Any] | None:
    """Query dex_events aggregated by pool for quick volume/liquidity stats."""

    def _query() -> list[tuple]:
        client = _get_ch_client()
        if client is None:
            return []

        params: dict[str, Any] = {
            "pool": pool_address,
            "chain": chain,
            "hours": hours_back,
        }

        try:
            return client.execute(
                """
                SELECT
                    contract_address,
                    count() AS total_swaps,
                    sum(toFloat64(amount0_in) + toFloat64(amount0_out)) AS vol0,
                    sum(toFloat64(amount1_in) + toFloat64(amount1_out)) AS vol1,
                    dex, version, token0, token1
                FROM wallet_memory.dex_events
                WHERE contract_address = %(pool)s
                  AND chain = %(chain)s
                  AND type = 'swap'
                  AND block_timestamp >= now() - INTERVAL %(hours)s HOUR
                GROUP BY contract_address, dex, version, token0, token1
                """,
                params,
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("pool_stats query failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows or not rows[0]:
        return None

    row = rows[0]
    try:
        return {
            "pool_address": row[0],
            "total_swaps": row[1],
            "volume_token0": float(row[2] or 0),
            "volume_token1": float(row[3] or 0),
            "dex": row[4],
            "version": row[5],
            "token0": row[6],
            "token1": row[7],
        }
    except (ValueError, TypeError, IndexError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# DATA TRANSFORMATION
# ═══════════════════════════════════════════════════════════════════════════


def _build_pairs(
    swaps: list[dict[str, Any]],
    our_token: str,
    chain: str,
) -> list[dict[str, Any]]:
    """Build DexScreener-compatible pairs from our dex_events data.

    Groups swaps by pool (contract_address) and computes:
      - volume_24h: sum of amounts for our token in the pair
      - liquidity_usd: estimated from largest swap (proxy)
      - price_usd: volume-weighted average price
      - txns_24h: buy/sell count (determined by direction)
      - price_change_24h: relative change from first to last swap
    """
    # Group swaps by pool
    pools: dict[str, list[dict[str, Any]]] = {}
    for swap in swaps:
        pool = swap["contract_address"]
        if pool not in pools:
            pools[pool] = []
        pools[pool].append(swap)

    pairs = []
    for pool_addr, pool_swaps in pools.items():
        if not pool_swaps:
            continue

        dex_info = pool_swaps[0]
        dex_name = f"{dex_info['dex']}_{dex_info['version']}"

        # Determine if our token is token0 or token1 in this pool
        is_token0 = dex_info["token0"].lower() == our_token

        # Compute volume for our token
        volume_24h = 0.0
        buy_count = 0
        sell_count = 0
        prices_weighted: list[tuple[float, float]] = []  # (price, weight)

        for swap in pool_swaps:
            if is_token0:
                # Our token is token0 → volume = amount0_in + amount0_out
                amt_in = float(swap["amount0_in"] or 0)
                amt_out = float(swap["amount0_out"] or 0)
                paired_in = float(swap["amount1_in"] or 0)
                paired_out = float(swap["amount1_out"] or 0)
            else:
                # Our token is token1 → volume = amount1_in + amount1_out
                amt_in = float(swap["amount1_in"] or 0)
                amt_out = float(swap["amount1_out"] or 0)
                paired_in = float(swap["amount0_in"] or 0)
                paired_out = float(swap["amount0_out"] or 0)

            volume_24h += amt_in + amt_out

            # Determine direction: buy = receiving our token
            is_buy = float(swap["amount0_out"] or 0) > 0 if is_token0 else float(swap["amount1_out"] or 0) > 0

            if is_buy:
                buy_count += 1
            else:
                sell_count += 1

            # Compute price: paired_token / our_token
            if amt_in + amt_out > 0:
                if is_token0:
                    # price = paired (token1) / our (token0)
                    if amt_out > 0:
                        price = (paired_in or 1) / amt_out if amt_out else 0
                    else:
                        price = (paired_out or 1) / amt_in if amt_in else 0
                else:
                    # price = paired (token0) / our (token1)
                    if amt_out > 0:
                        price = (paired_in or 1) / amt_out if amt_out else 0
                    else:
                        price = (paired_out or 1) / amt_in if amt_in else 0

                if price > 0 and price < 1_000_000:  # Sanity filter
                    prices_weighted.append((price, amt_in + amt_out))

        # Volume-weighted average price
        price_usd = 0.0
        if prices_weighted:
            total_weight = sum(w for _, w in prices_weighted)
            if total_weight > 0:
                price_usd = sum(p * w for p, w in prices_weighted) / total_weight

        # Price change: compare first vs last swap price
        price_change_24h = 0.0
        if len(prices_weighted) > 1:
            first_price = prices_weighted[0][0]
            last_price = prices_weighted[-1][0]
            if first_price > 0:
                price_change_24h = ((last_price - first_price) / first_price) * 100

        # Liquidity proxy: use largest swap amount * 2 (crude estimate)
        max_swap_volume = (
            max(
                (
                    float(s["amount0_in"] or 0) + float(s["amount0_out"] or 0)
                    if is_token0
                    else float(s["amount1_in"] or 0) + float(s["amount1_out"] or 0)
                )
                for s in pool_swaps
            )
            if pool_swaps
            else 0
        )
        # Scale liquidity estimate: typical pool has ~10x daily volume in liquidity
        liquidity_est = max(max_swap_volume * 100, volume_24h * 0.3)

        pair_entry = {
            "chainId": chain,
            "dexId": dex_name,
            "pairAddress": pool_addr,
            "priceUsd": str(round(price_usd, 18)) if price_usd > 0 else "0",
            "priceNative": "",  # We don't track native token price directly
            "volume": {
                "h24": round(volume_24h, 2),
                "h6": round(volume_24h * 0.25, 2),  # Rough estimate
                "h1": round(volume_24h * 0.04, 2),  # Rough estimate
            },
            "liquidity": {
                "usd": round(liquidity_est, 2),
                "base": str(round(volume_24h * 0.5, 2)),  # Rough estimate
                "quote": str(round(volume_24h * 0.5, 2)),  # Rough estimate
            },
            "txns": {
                "h24": {
                    "buys": buy_count,
                    "sells": sell_count,
                }
            },
            "priceChange": {
                "h24": round(price_change_24h, 2),
            },
            "labels": ["v3" if "v3" in dex_name else "v2"],
        }

        # FDV — only if we can derive price and have some sense of supply
        # We skip FDV here since we don't track circulating supply
        pair_entry["fdv"] = 0

        pairs.append(pair_entry)

    # Sort by volume descending (DexScreener returns highest-liquidity first)
    pairs.sort(key=lambda p: p["volume"]["h24"], reverse=True)
    return pairs


def _parse_decimal(value: Any) -> Decimal | None:
    """Safely parse a decimal value from ClickHouse."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError):
        return None


# ═══════════════════════════════════════════════════════════════════════════
# FALLBACK TO DEXSCREENER API
# ═══════════════════════════════════════════════════════════════════════════


async def _fallback_to_dexscreener(token_address: str) -> dict[str, Any] | None:
    """Fall back to DexScreener API when ClickHouse has insufficient data.

    Uses the same error/resilience pattern as _fetch_dexscreener in _core.py.
    """
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.dexscreener.com/latest/dex/tokens/{token_address}",
            )
            if resp.status_code == 200:
                data = resp.json()
                if data and data.get("pairs"):
                    data["source"] = "fallback"
                    data["metrics"] = {
                        "total_volume_24h": sum(float(p.get("volume", {}).get("h24", 0) or 0) for p in data["pairs"]),
                        "swap_count_24h": sum(
                            int(p.get("txns", {}).get("h24", {}).get("buys", 0) or 0)
                            + int(p.get("txns", {}).get("h24", {}).get("sells", 0) or 0)
                            for p in data["pairs"]
                        ),
                        "unique_pools": len(data["pairs"]),
                    }
                    logger.info("DexScreener fallback used for %s", token_address)
                    return data
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError) as exc:
        logger.debug("DexScreener fallback failed for %s: %s", token_address, exc)

    return None


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY — Freshenss Check
# ═══════════════════════════════════════════════════════════════════════════


async def check_data_freshness(chain: str = "ethereum") -> dict[str, Any]:
    """Check how fresh the dex_events data is for a given chain.

    Returns:
        {
            "latest_block_timestamp": "2026-07-30T12:00:00",
            "age_minutes": 5.2,
            "event_count_24h": 12345,
            "healthy": True,
        }
    """

    def _query() -> list[tuple]:
        client = _get_ch_client()
        if client is None:
            return []
        try:
            return client.execute(
                """
                SELECT
                    max(block_timestamp) AS latest_ts,
                    count() AS event_count,
                    max(block_number) AS latest_block
                FROM wallet_memory.dex_events
                WHERE chain = %(chain)s
                  AND block_timestamp >= now() - INTERVAL 24 HOUR
                """,
                {"chain": chain},
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("freshness check failed: %s", exc)
            return []

    rows = await _run_sync(_query)
    if not rows or not rows[0]:
        return {"healthy": False, "error": "Could not query data freshness"}

    row = rows[0]
    latest_ts = row[0]
    age_minutes = 0.0
    if latest_ts and hasattr(latest_ts, "timestamp"):
        age_minutes = (time.time() - latest_ts.timestamp()) / 60

    return {
        "chain": chain,
        "latest_block_timestamp": latest_ts.isoformat() if hasattr(latest_ts, "isoformat") else str(latest_ts),
        "age_minutes": round(age_minutes, 1),
        "event_count_24h": row[1],
        "latest_block": row[2],
        "healthy": age_minutes < 60,  # Healthy if data < 1 hour old
    }


# ═══════════════════════════════════════════════════════════════════════════
# HELPER — Quick price for a token (used by own_price_oracle)
# ═══════════════════════════════════════════════════════════════════════════


async def get_token_price_simple(
    token_address: str,
    chain: str,
    lookback_minutes: int = 60,
) -> dict[str, Any]:
    """Quick TWAP price from dex_events.

    Returns:
        {
            "price_usd": 0.001234,
            "confidence": 0.85,  (0-1, based on swap count and spread)
            "swap_count": 42,
            "time_span_minutes": 55,
            "source": "own_dex_events",
        }
    """
    token_address = token_address.lower()
    chain = _normalize_chain(chain)

    swaps = await _query_dex_swaps(token_address, chain, lookback_minutes // 60 + 1)

    # Filter to lookback window by timestamp
    cutoff = time.time() - lookback_minutes * 60
    recent_swaps = [s for s in swaps if s["timestamp"] >= cutoff]

    if len(recent_swaps) < 2:
        return {
            "price_usd": 0.0,
            "confidence": 0.0,
            "swap_count": len(recent_swaps),
            "time_span_minutes": 0,
            "source": "insufficient_data",
        }

    # Volume-weighted average price
    prices_weighted: list[tuple[float, float]] = []
    for swap in recent_swaps:
        is_token0 = swap["token0"].lower() == token_address
        if is_token0:
            amt_out = float(swap["amount0_out"] or 0)
            paired_in = float(swap["amount1_in"] or 0)
            if amt_out > 0:
                price = (paired_in or 0) / amt_out
                if 0 < price < 1_000_000:
                    prices_weighted.append((price, amt_out))
        else:
            amt_out = float(swap["amount1_out"] or 0)
            paired_in = float(swap["amount0_in"] or 0)
            if amt_out > 0:
                price = (paired_in or 0) / amt_out
                if 0 < price < 1_000_000:
                    prices_weighted.append((price, amt_out))

    if not prices_weighted:
        return {
            "price_usd": 0.0,
            "confidence": 0.0,
            "swap_count": len(recent_swaps),
            "time_span_minutes": 0,
            "source": "no_valid_price",
        }

    total_weight = sum(w for _, w in prices_weighted)
    vwap = sum(p * w for p, w in prices_weighted) / total_weight if total_weight > 0 else 0

    # Confidence based on swap count and price consistency
    prices_only = [p for p, _ in prices_weighted]
    price_spread = (max(prices_only) - min(prices_only)) / (sum(prices_only) / len(prices_only)) if prices_only else 1.0
    swap_confidence = min(1.0, len(prices_weighted) / 50)  # More swaps = higher confidence
    spread_penalty = min(1.0, price_spread / 50)  # High spread = lower confidence
    confidence = max(0.0, min(1.0, swap_confidence * (1 - spread_penalty)))

    time_span = (recent_swaps[0]["timestamp"] - recent_swaps[-1]["timestamp"]) / 60 if len(recent_swaps) > 1 else 0

    return {
        "price_usd": round(vwap, 18),
        "confidence": round(confidence, 4),
        "swap_count": len(prices_weighted),
        "time_span_minutes": round(time_span, 1),
        "source": "own_dex_events",
    }


# ═══════════════════════════════════════════════════════════════════════════
# METRICS EXPORT
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return current cache statistics for monitoring."""
    return {
        "cache_size": len(_DEX_CACHE),
        "cache_max": _DEX_CACHE_MAX,
        "clickhouse_connected": _ch_client is not None,
    }


def clear_cache() -> None:
    """Clear the in-process cache (for testing)."""
    _DEX_CACHE.clear()
