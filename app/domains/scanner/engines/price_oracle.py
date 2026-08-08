"""
F2 Price / Decimals Oracle — USD Normalization Layer
=====================================================
Every RugMunchScan capability that computes USD values depends on this
module: wallet flow (C1), holder clustering (C2), attack detection (C6),
and deployer fingerprint (C7).

Tiered price resolution:
  1. OWN DATA: derive price from wallet_memory.dex_events reserve ratios
     (amount1_out / amount0_in for any pair we've indexed — 59M rows, free).
     Ratios are decimals-normalized and anchored to USD via stablecoin or
     native-wrapped pairs.
  2. FREE APIs (verified 2026-08-02, no auth):
       DexScreener  -> https://api.dexscreener.com/latest/dex/tokens/{addr}
       CoinGecko    -> https://api.coingecko.com/api/v3/simple/token_price/{platform}
       DeFiLlama    -> https://coins.llama.fi/prices/current/{chain}:{addr}
       GeckoTerminal-> https://api.geckoterminal.com/api/v2/networks/{net}/tokens/{addr}
       ParaSwap     -> https://apiv5.paraswap.io/prices (free, no auth)
       (1inch is FREEMIUM — only used when ONEINCH_API_KEY is set)
  3. Cache: Redis (`price:{chain}:{address}`, TTL 300s) + ClickHouse
     `wallet_memory.token_meta` table (ReplacingMergeTree).

Public API:
    price = await get_token_price("0x...", "ethereum")
    # -> {"price_usd": 0.001234, "source": "own_dex_events",
    #     "timestamp": 1720000000, "confidence": 0.85}
    decimals = await get_token_decimals("0x...", "ethereum")     # 6 / 18 / ...
    native = await get_native_price("ethereum")                  # USD per ETH
    usd = await normalize_amount("0x...", "ethereum", 1_000_000) # -> USD

All functions are defensive: type-hinted, docstringed, wrapped in
try/except, and fail gracefully (return None / 0.0) when ClickHouse or
the free APIs are unreachable.

NOTE (degodding 2026-08-08): 1,595 lines. The FREE API TIERS (DexScreener /
CoinGecko / DeFiLlama / GeckoTerminal / ParaSwap / 1inch) and the NATIVE PRICE
FALLBACKS were extracted into ``engines/_price_adapters.py``; the chain
normalization + CoinGecko native-id tables moved with them and are re-imported
here. This module keeps the tier resolution, ClickHouse / Redis / token_meta
cache layers, the OWN-DATA tiers, and the public API (get_token_price,
get_native_price, normalize_amount) in place so the ~10 callers are unaffected.
"""

from __future__ import annotations

import math
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from redis.exceptions import RedisError

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

if TYPE_CHECKING:
    from collections.abc import Callable

from app.core.logging import get_logger
from app.domains.scanner.engines._common import (
    clear_cache as _common_clear_cache,
    get_cache_stats as _common_get_cache_stats,
    run_sync,
)
from app.domains.scanner.engines._price_adapters import (
    _CHAIN_MAP,
    _coingecko_price,
    _decimals_from_dexscreener,
    _defillama_price,
    _dexscreener_price,
    _geckoterminal_price,
    _native_coingecko,
    _native_defillama,
    _oneinch_price,
    _paraswap_price,
)

logger = get_logger(__name__)

# ── Cache TTLs ─────────────────────────────────────────────────────────────
_PRICE_TTL = 300  # Redis + token_meta price cache TTL (seconds)
_MEM_TTL = 60  # in-process price cache TTL (seconds)
_MEM_MAX = 500  # in-process cache max entries (LRU eviction)
_NATIVE_TTL = 300  # native price cache TTL (seconds)

# ── Wrapped native token addresses per chain (for native price resolution) ──
_WRAPPED_NATIVE: dict[str, str] = {
    "ethereum": "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",  # WETH
    "bsc": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",  # WBNB
    "polygon": "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270",  # WMATIC
    "arbitrum": "0x82af49447d8a07e3bd95bd0d56f35241523fbab1",  # WETH
    "optimism": "0x4200000000000000000000000000000000000006",  # WETH
    "base": "0x4200000000000000000000000000000000000006",  # WETH
    "avalanche": "0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7",  # WAVAX
    "fantom": "0x21be370d5312f44cb42ce377bc9b8a0cef1a4c83",  # WFTM
    "linea": "0xe5d7c2a44ffddf6b295a15c148167daaaf5cf34f",  # WETH
    "gnosis": "0xe91d153e0b41518a2ce8dd3d7944fa863463a97d",  # WXDAI
    "mantle": "0x78c1b0c915c4faa5fffa6cabf0219da63d7f4cb8",  # WMNT
    "solana": "So11111111111111111111111111111111111111112",  # WSOL
}

# ── Stablecoin addresses (multi-chain) used to anchor own-data prices to USD ──
_STABLECOINS: set[str] = {
    # ethereum
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7",  # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f",  # DAI
    "0x4fabb145d64652a948d72533023f6e7a623c7c53",  # BUSD
    "0x853d955acef822db058eb8505911ed77f175b99e",  # FRAX
    "0x57ab1ec28d129707052df4df418d58a2d46d5f51",  # USDS (sUSDS)
    # base
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC
    "0x50c5725949a6f0c72e6c4a641f24049a917db0cb",  # DAI
    # arbitrum
    "0xaf88d065e77c8cc2239327c5edb3a432268e5831",  # USDC
    "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9",  # USDT
    # optimism
    "0x0b2c639c533813f4aa9d7837caf62653d097ff85",  # USDC
    "0x94b008aa00579c1307b0ef2c499ad98a8ce58e58",  # USDT
    "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1",  # DAI
    # polygon
    "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",  # USDC
    "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",  # USDT
    "0x8f3cf7ad23cd3cadbd9735aff958023239c6a063",  # DAI
    # avalanche
    "0xb97ef9ef8734c71904d8002f8b6bc66dd9c48a6e",  # USDC
    "0x9702230a8ea53601f5cd2dc00fdbc13d4df4a8c7",  # USDT
    # fantom
    "0x04068da6c83afcfa0e13ba15a6696662335d5b75",  # USDC
    # bsc
    "0x55d398326f99059ff775485246999027b3197955",  # USDT
    "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",  # USDC
    # solana
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KjNkEN6kiFnSkHM6hTzM",  # USDT
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",  # USDe
}

# ── Decimals fallback map (spec: ETH=18, USDC=6, USDT=6, SOL=9) ────────────
# Layered over own_dex_data.format._get_token_decimals which already covers
# a wide known-token table + ClickHouse value-pattern estimation.
_FALLBACK_DECIMALS: dict[str, dict[str, int]] = {
    "solana": {
        "So11111111111111111111111111111111111111112": 9,  # WSOL
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": 6,  # USDC
        "Es9vMFrzaCERmJfrF4H2FYD4KjNkEN6kiFnSkHM6hTzM": 6,  # USDT
        "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263": 9,  # USDe
    },
    "ethereum": {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": 18,  # WETH
        "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee": 18,  # ETH (native alias)
    },
    "bsc": {"0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c": 18},  # WBNB
    "polygon": {"0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270": 18},  # WMATIC
    "arbitrum": {"0x82af49447d8a07e3bd95bd0d56f35241523fbab1": 18},  # WETH
    "base": {"0x4200000000000000000000000000000000000006": 18},  # WETH
    "avalanche": {"0xb31f66aa3c1e785363f0875a1b74e27b85fd66c7": 18},  # WAVAX
    "fantom": {"0x21be370d5312f44cb42ce377bc9b8a0cef1a4c83": 18},  # WFTM
    "solana_native_alias": {},
}

# ── In-process caches ─────────────────────────────────────────────────────
_price_mem: dict[str, dict[str, Any]] = {}
_decimals_mem: dict[str, int] = {}
_native_mem: dict[str, dict[str, Any]] = {}

# ── Lazy clients ──────────────────────────────────────────────────────────
_ch_client: Any = None
_redis_client: Any = None


def _normalize_chain(chain: str) -> str:
    """Normalize chain name to ClickHouse / provider format."""
    return _CHAIN_MAP.get(chain.lower(), chain.lower())


def _normalize_token(token_address: str, chain: str) -> str:
    """Normalize token address for lookups.

    EVM addresses are lowercased; Solana base58 addresses are
    case-sensitive and preserved as-is.
    """
    token_address = str(token_address).strip()
    if chain == "solana":
        return token_address
    return token_address.lower()


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE CLIENT + token_meta TABLE
# ═══════════════════════════════════════════════════════════════════════════


def _get_ch_client() -> Any | None:
    """Lazy-init ClickHouse native client for the wallet_memory database."""
    global _ch_client
    if _ch_client is not None:
        return _ch_client

    ch_host = os.getenv("CLICKHOUSE_HOST") or os.getenv("CH_HOST", "rmi-clickhouse")
    ch_port = int(os.getenv("CLICKHOUSE_PORT") or os.getenv("CH_PORT") or "9000")
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
        logger.info("price_oracle ClickHouse connected: %s:%s/%s", ch_host, ch_port, ch_db)
        return _ch_client
    except (ImportError, ConnectionError, TimeoutError, RuntimeError, OSError, CHDriverError) as exc:
        logger.warning("price_oracle ClickHouse unavailable: %s", exc)
        _ch_client = None
        return None


async def ensure_token_meta_table() -> bool:
    """Ensure the ClickHouse token_prices table exists.

    ``wallet_memory.token_meta`` is now a VIEW over ``token_meta_ref`` +
    ``token_prices`` (see the lite-chain-indexer migration), so the price
    oracle persists into the underlying ``token_prices`` ReplacingMergeTree
    table instead. This function creates it idempotently.

    Returns True when the table is guaranteed present (or already was),
    False when ClickHouse is unreachable.
    """

    def _ensure() -> bool:
        client = _get_ch_client()
        if client is None:
            return False
        try:
            client.execute(
                """
                CREATE TABLE IF NOT EXISTS wallet_memory.token_prices (
                    chain LowCardinality(String),
                    token_address String,
                    symbol String DEFAULT '',
                    price_usd Float64,
                    updated_at DateTime DEFAULT now()
                ) ENGINE = ReplacingMergeTree()
                ORDER BY (chain, token_address)
                """
            )
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("token_prices ensure failed: %s", exc)
            return False

    return bool(await run_sync(_ensure))


async def _meta_upsert(
    token_address: str,
    chain: str,
    *,
    symbol: str = "",
    price_usd: float | None = None,
) -> None:
    """Upsert a token_prices row (ReplacingMergeTree replaces on merge).

    Writes to ``wallet_memory.token_prices`` — ``token_meta`` is a VIEW
    over token_meta_ref + token_prices and cannot accept INSERTs.
    Name/decimals live in token_meta_ref; only price + symbol go here.
    """

    def _write() -> None:
        client = _get_ch_client()
        if client is None:
            return
        try:
            client.execute(
                """
                INSERT INTO wallet_memory.token_prices
                    (chain, token_address, symbol, price_usd, updated_at)
                VALUES
                """,
                [(chain, token_address, symbol, price_usd, datetime.now(UTC))],
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("token_prices upsert failed for %s: %s", token_address[:16], exc)

    await run_sync(_write)


async def _meta_get(token_address: str, chain: str) -> dict[str, Any] | None:
    """Read the latest token_meta row (by updated_at) for a token."""

    def _read() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []
        try:
            return list(
                client.execute(
                    """
                    SELECT token_address, chain, symbol, name, decimals,
                           last_price_usd, last_price_source, updated_at
                    FROM wallet_memory.token_meta
                    WHERE token_address = %(token)s AND chain = %(chain)s
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    {"token": token_address, "chain": chain},
                )
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("token_meta read failed for %s: %s", token_address[:16], exc)
            return []

    rows = await run_sync(_read)
    if not rows:
        return None
    row = rows[0]
    return {
        "token_address": row[0],
        "chain": row[1],
        "symbol": row[2],
        "name": row[3],
        "decimals": row[4],
        "last_price_usd": row[5],
        "last_price_source": row[6],
        "updated_at": row[7].timestamp() if hasattr(row[7], "timestamp") else time.time(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# REDIS CACHE HELPERS
# ═══════════════════════════════════════════════════════════════════════════


async def _get_redis() -> Any | None:
    """Lazy async Redis client (graceful when Redis is unreachable)."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        host = os.getenv("REDIS_HOST", "rmi-redis")
        port = int(os.getenv("REDIS_PORT", "6379"))
        password = os.getenv("REDIS_PASSWORD") or None
        _redis_client = aioredis.Redis(
            host=host,
            port=port,
            password=password,
            db=int(os.getenv("REDIS_DB", "0")),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=3,
        )
        return _redis_client
    except (ImportError, OSError, ValueError) as exc:
        logger.debug("price_oracle Redis init failed: %s", exc)
        _redis_client = None
        return None


async def _redis_get(key: str) -> dict[str, Any] | None:
    """Read a JSON dict from Redis (None on miss / failure)."""
    try:
        import json

        r = await _get_redis()
        if r is None:
            return None
        raw = await r.get(key)
        if not raw:
            return None
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("price_oracle Redis get failed: %s", exc)
        return None


async def _redis_set(key: str, value: dict[str, Any], ttl: int = _PRICE_TTL) -> None:
    """Write a JSON dict to Redis with TTL (best effort)."""
    try:
        import json

        r = await _get_redis()
        if r is None:
            return
        await r.set(key, json.dumps(value), ex=ttl)
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("price_oracle Redis set failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def get_token_price(
    token_address: str,
    chain: str,
    *,
    force_fresh: bool = False,
    _depth: int = 0,
) -> dict[str, Any] | None:
    """Resolve a token's USD price through the tiered oracle.

    Tier order:
      1. Caches: in-process -> Redis -> ClickHouse token_meta
      2. OWN DATA: dex_events reserve ratios (decimals-aware, USD-anchored)
      3. DexScreener (free)
      4. CoinGecko (free, rate-limited)
      5. DeFiLlama (free)
      6. GeckoTerminal (free)
      7. ParaSwap (free) / 1inch (freemium)

    Args:
        token_address: Token contract address (EVM lowercased; Solana preserved)
        chain: Chain name (ethereum, bsc, solana, ...)
        force_fresh: Bypass all caches and re-resolve
        _depth: Internal recursion guard for paired-token anchoring (max 2)

    Returns:
        {
            "price_usd": float,
            "source": str,       # own_dex_events | dexscreener | coingecko | ...
            "timestamp": float,  # epoch seconds of resolution
            "confidence": float, # 0-1
        }
        or None when every tier fails (ClickHouse unreachable, APIs down).
    """
    token = _normalize_token(token_address, chain)
    chain = _normalize_chain(chain)
    if not token:
        return None
    _start = time.monotonic()

    # ── 1. Caches ──
    if not force_fresh:
        cached = await _get_cached_price(token, chain, _depth)
        if cached:
            return cached

    # ── 2..N. Live resolution ──
    for source_name, fetcher in _TIERS:
        try:
            result = await fetcher(token, chain, _depth=_depth)
        except (
            httpx.HTTPError,
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            CHDriverError,
        ) as exc:  # defensive: a provider must never crash the oracle
            logger.debug("price_oracle tier %s failed for %s: %s", source_name, token[:16], exc)
            result = None
        if not result:
            continue
        price_usd = float(result.get("price_usd", 0) or 0)
        if price_usd <= 0:
            continue

        payload: dict[str, Any] = {
            "price_usd": price_usd,
            "source": source_name,
            "timestamp": time.time(),
            "confidence": float(result.get("confidence", 0.5) or 0.5),
        }
        # Optional enrichment when the tier provided it
        for extra in ("volume_24h_usd", "market_cap_usd", "price_change_24h_pct", "symbol", "name", "decimals"):
            if result.get(extra) is not None:
                payload[extra] = result[extra]

        await _cache_set_price(token, chain, payload)
        _elapsed_ms = (time.monotonic() - _start) * 1000
        logger.info(
            "price_oracle_latency",
            ms=round(_elapsed_ms, 1),
            token=token[:16],
            chain=chain,
            source=source_name,
            price_usd=round(price_usd, 10),
            depth=_depth,
        )
        return payload

    logger.info(
        "price_oracle_latency",
        ms=round((time.monotonic() - _start) * 1000, 1),
        token=token[:16],
        chain=chain,
        source="unavailable",
        price_usd=0.0,
        depth=_depth,
    )
    return None


async def get_token_price_at_time(
    token_address: str,
    chain: str,
    at_time: datetime,
    *,
    window_minutes: int = 30,
    max_swaps: int = 200,
) -> dict[str, Any] | None:
    """Resolve a token's USD price near a specific historical timestamp.

    Uses own ClickHouse ``dex_events`` when swaps exist in the window around
    ``at_time``; falls back to the current price when no historical data is
    available (the correlation engine treats a fallback with
    ``source="current_fallback"`` as lower-confidence evidence).

    Args:
        token_address: Token contract address (EVM lowercased; Solana preserved)
        chain: Chain name (ethereum, bsc, solana, ...)
        at_time: Historical timestamp to resolve price around
        window_minutes: Half-window; swaps within at_time ± window are used
        max_swaps: Maximum number of swaps to consider for the VWAP

    Returns:
        {
            "price_usd": float,
            "source": str,       # own_dex_events_at_time | current_fallback
            "timestamp": float,  # epoch seconds of the resolved price
            "confidence": float, # 0-1
            "swap_count": int,
            "window_minutes": int,
        }
        or None when every tier fails.
    """
    token = _normalize_token(token_address, chain)
    chain = _normalize_chain(chain)
    if not token:
        return None

    # Normalize the requested timestamp to epoch seconds.
    if isinstance(at_time, datetime):
        try:
            at_ts = at_time.timestamp()
        except (OverflowError, OSError, ValueError):
            at_ts = float(time.time())
    else:
        at_ts = float(at_time or time.time())

    window_secs = max(1, int(window_minutes) * 60)
    half = window_secs // 2
    lo_ts = at_ts - half
    hi_ts = at_ts + half

    swaps = await _query_swaps_between(token, chain, lo_ts, hi_ts, max_swaps=max_swaps)
    if len(swaps) >= 2:
        price = await _vwap_price_from_swaps(token, chain, swaps)
        if price is not None and price.get("price_usd", 0) > 0:
            return {
                "price_usd": float(price["price_usd"]),
                "source": "own_dex_events_at_time",
                "timestamp": float(at_ts),
                "confidence": float(price.get("confidence", 0.5)),
                "swap_count": len(swaps),
                "window_minutes": int(window_minutes),
            }

    # Fallback: current price (best effort, flagged as low confidence)
    try:
        current = await get_token_price(token, chain)
        if current and float(current.get("price_usd", 0) or 0) > 0:
            return {
                "price_usd": float(current["price_usd"]),
                "source": "current_fallback",
                "timestamp": float(at_ts),
                "confidence": 0.3,
                "swap_count": 0,
                "window_minutes": int(window_minutes),
            }
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("price_oracle at-time fallback failed for %s: %s", token[:16], exc)

    return None


async def _query_swaps_between(
    token: str,
    chain: str,
    lo_ts: float,
    hi_ts: float,
    *,
    max_swaps: int = 200,
) -> list[dict[str, Any]]:
    """Query dex_events swaps for a token within a historical time range."""

    def _query() -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []
        try:
            return list(
                client.execute(
                    """
                    SELECT
                        contract_address, dex, version,
                        token0, token1,
                        amount0_in, amount1_in, amount0_out, amount1_out,
                        block_timestamp, tx_hash
                    FROM wallet_memory.dex_events
                    WHERE chain = %(chain)s
                      AND type = 'swap'
                      AND block_timestamp >= toDateTime(%(lo)s)
                      AND block_timestamp <= toDateTime(%(hi)s)
                      AND (token0 = %(token)s OR token1 = %(token)s)
                    ORDER BY block_timestamp ASC
                    LIMIT %(limit)s
                    """,
                    {
                        "chain": chain,
                        "token": token,
                        "lo": int(lo_ts),
                        "hi": int(hi_ts),
                        "limit": max_swaps,
                    },
                )
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("price_oracle at-time swap query failed for %s: %s", token[:16], exc)
            return []

    rows = await run_sync(_query)
    if not rows:
        return []

    swaps: list[dict[str, Any]] = []
    for row in rows:
        try:
            swaps.append(
                {
                    "contract_address": row[0],
                    "dex": row[1],
                    "version": row[2],
                    "token0": row[3],
                    "token1": row[4],
                    "amount0_in": float(row[5] or 0),
                    "amount1_in": float(row[6] or 0),
                    "amount0_out": float(row[7] or 0),
                    "amount1_out": float(row[8] or 0),
                    "block_timestamp": row[9],
                    "tx_hash": row[10],
                }
            )
        except (ValueError, TypeError, IndexError) as exc:
            logger.debug("price_oracle at-time row parse failed: %s", exc)
            continue
    return swaps


async def _vwap_price_from_swaps(
    token: str,
    chain: str,
    swaps: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Compute a USD-anchored VWAP from a list of raw swap dicts.

    Mirrors ``_own_price_vwap`` logic but operates on an explicit swap list
    (already time-bounded by the caller) instead of re-querying ClickHouse.
    """
    if not swaps:
        return None

    prices_weighted: list[tuple[float, float]] = []
    paired_counts: dict[str, int] = {}
    token_lower = str(token).lower()

    for swap in swaps:
        t0 = str(swap.get("token0") or "").lower()
        t1 = str(swap.get("token1") or "").lower()
        if not t0 or not t1:
            continue
        is_token0 = t0 == token_lower
        if not is_token0 and t1 != token_lower:
            continue
        paired = t1 if is_token0 else t0
        if not paired:
            continue
        paired_counts[paired] = paired_counts.get(paired, 0) + 1

        try:
            if is_token0:
                amt_in = float(swap.get("amount0_in") or 0)
                amt_out = float(swap.get("amount0_out") or 0)
                paired_in = float(swap.get("amount1_in") or 0)
                paired_out = float(swap.get("amount1_out") or 0)
            else:
                amt_in = float(swap.get("amount1_in") or 0)
                amt_out = float(swap.get("amount1_out") or 0)
                paired_in = float(swap.get("amount0_in") or 0)
                paired_out = float(swap.get("amount0_out") or 0)
        except (ValueError, TypeError):
            continue

        weight = amt_in + amt_out
        if weight <= 0:
            continue
        # buy: our token out, paired in; sell: our token in, paired out
        if amt_out > 0 and paired_in > 0:
            price = paired_in / amt_out
        elif amt_in > 0 and paired_out > 0:
            price = paired_out / amt_in
        else:
            continue
        if 0 < price < 1_000_000_000:
            prices_weighted.append((price, weight))

    if not prices_weighted:
        return None

    total_weight = sum(w for _, w in prices_weighted)
    vwap = sum(p * w for p, w in prices_weighted) / total_weight if total_weight > 0 else 0.0
    paired_token = max(paired_counts, key=lambda k: paired_counts[k]) if paired_counts else ""
    if vwap <= 0 or not paired_token:
        return None

    # Decimals-normalize: raw = paired_raw / our_raw
    our_decimals = await get_token_decimals(token, chain)
    paired_decimals = await get_token_decimals(paired_token, chain)
    if our_decimals is None or paired_decimals is None:
        return None
    norm_price = vwap * math.pow(10, our_decimals) / math.pow(10, paired_decimals)

    usd_price, confidence, anchor = await _anchor_to_usd(norm_price, paired_token, chain, 0)
    if usd_price <= 0:
        return None

    evidence = min(1.0, len(prices_weighted) / 50)
    confidence = max(0.05, min(1.0, confidence * (0.5 + 0.5 * evidence)))
    return {
        "price_usd": usd_price,
        "confidence": round(confidence, 4),
        "swap_count": len(prices_weighted),
        "paired_token": paired_token,
        "anchor": anchor,
    }


async def get_token_decimals(token_address: str, chain: str) -> int | None:
    """Resolve the number of decimals for a token.

    Resolution order:
      1. In-process cache
      2. ClickHouse token_meta (decimals column)
      3. DexScreener pair response (baseToken/quoteToken decimals)
      4. Hardcoded fallback map (ETH=18, USDC=6, USDT=6, SOL=9, wrapped natives)
      5. own_dex_data.format._get_token_decimals (known tokens + estimation)
      6. Default: 9 for Solana, 18 for EVM

    Args:
        token_address: Token contract address
        chain: Chain name

    Returns:
        int decimals, or None when unresolvable (ClickHouse + APIs unreachable
        and no fallback applies).
    """
    token = _normalize_token(token_address, chain)
    chain = _normalize_chain(chain)
    if not token:
        return None

    cache_key = f"{chain}:{token}"
    if cache_key in _decimals_mem:
        return _decimals_mem[cache_key]

    # ── ClickHouse token_meta ──
    try:
        meta = await _meta_get(token, chain)
        if meta and meta.get("decimals") is not None:
            _decimals_mem[cache_key] = int(meta["decimals"])
            return int(meta["decimals"])
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("token_meta decimals read failed for %s: %s", token[:16], exc)

    # ── DexScreener pair response ──
    decimals = await _decimals_from_dexscreener(token, chain)
    if decimals is not None:
        _decimals_mem[cache_key] = decimals
        return decimals

    # ── Hardcoded fallback map ──
    chain_map = _FALLBACK_DECIMALS.get(chain, {})
    if token in chain_map:
        _decimals_mem[cache_key] = chain_map[token]
        return chain_map[token]

    # ── own_dex_data known-token table + ClickHouse estimation ──
    try:
        from app.domains.scanner.engines.own_dex_data.format import _get_token_decimals

        known = _get_token_decimals(token, chain)
        if known is not None:
            _decimals_mem[cache_key] = known
            return known
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("own_dex_data decimals lookup failed for %s: %s", token[:16], exc)

    # ── Default ──
    default = 9 if chain == "solana" else 18
    _decimals_mem[cache_key] = default
    if len(_decimals_mem) > _MEM_MAX:
        _decimals_mem.pop(next(iter(_decimals_mem)), None)
    return default


async def get_native_price(chain: str) -> float:
    """Resolve the native coin price in USD for a chain (ETH, BNB, SOL, ...).

    Resolution order:
      1. In-process cache (TTL 5m)
      2. get_token_price(wrapped_native, chain)
      3. CoinGecko simple/price by native coin id
      4. DeFiLlama current price by chain id (ethereum, bsc, solana, ...)

    Returns 0.0 when every tier fails (never raises).
    """
    chain = _normalize_chain(chain)
    if chain in _native_mem:
        cached = _native_mem[chain]
        if time.time() - cached.get("_cached_at", 0) < _NATIVE_TTL:
            return float(cached.get("price_usd", 0) or 0)

    # ── Wrapped native via the token oracle ──
    wrapped = _WRAPPED_NATIVE.get(chain)
    if wrapped:
        try:
            price_result = await get_token_price(wrapped, chain, force_fresh=True)
            if price_result and price_result.get("price_usd", 0) > 0:
                price = float(price_result["price_usd"])
                _native_mem[chain] = {"price_usd": price, "_cached_at": time.time()}
                return price
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
            logger.debug("native price via wrapped token failed for %s: %s", chain, exc)

    # ── CoinGecko by native coin id ──
    price = await _native_coingecko(chain)
    if price > 0:
        _native_mem[chain] = {"price_usd": price, "_cached_at": time.time()}
        return price

    # ── DeFiLlama native (ethereum / bsc / solana / ...) ──
    price = await _native_defillama(chain)
    if price > 0:
        _native_mem[chain] = {"price_usd": price, "_cached_at": time.time()}
        return price

    return 0.0


async def normalize_amount(token_address: str, chain: str, raw_amount: int | float) -> float | None:
    """Convert a raw on-chain amount (wei / lamports / base units) to USD.

    USD = (raw_amount / 10^decimals) * price_usd

    Args:
        token_address: Token contract address
        chain: Chain name
        raw_amount: Raw amount in the token's smallest unit

    Returns:
        USD float value, or None when the price or decimals cannot be
        resolved (or raw_amount is invalid).
    """
    try:
        raw = float(raw_amount)
    except (ValueError, TypeError):
        return None
    if raw <= 0:
        return None

    price = await get_token_price(token_address, chain)
    if not price:
        return None
    price_usd = float(price.get("price_usd", 0) or 0)
    if price_usd <= 0:
        return None

    decimals = await get_token_decimals(token_address, chain)
    if decimals is None:
        return None

    human_amount = raw / math.pow(10, decimals)
    return human_amount * price_usd


# ═══════════════════════════════════════════════════════════════════════════
# CACHE LAYERS (in-process -> Redis -> ClickHouse token_meta)
# ═══════════════════════════════════════════════════════════════════════════


async def _get_cached_price(token: str, chain: str, depth: int) -> dict[str, Any] | None:
    """Return a cached price from in-process, Redis, or token_meta (fresh only)."""
    cache_key = f"{chain}:{token}"

    # in-process
    if cache_key in _price_mem:
        cached = _price_mem[cache_key]
        if time.time() - cached.get("_cached_at", 0) < _MEM_TTL:
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    # Redis
    try:
        redis_val = await _redis_get(f"price:{chain}:{token}")
        if redis_val and float(redis_val.get("price_usd", 0) or 0) > 0:
            redis_val["source"] = f"redis_cache:{redis_val.get('source', 'unknown')}"
            _price_mem[cache_key] = {**redis_val, "_cached_at": time.time()}
            return redis_val
    except (ConnectionError, OSError, TimeoutError, ValueError, RedisError) as exc:
        logger.debug("price_oracle Redis cache read failed: %s", exc)

    # ClickHouse token_meta (only when fresh within TTL)
    if depth == 0:
        try:
            meta = await _meta_get(token, chain)
            if (
                meta
                and meta.get("last_price_usd") is not None
                and float(meta.get("last_price_usd") or 0) > 0
                and time.time() - float(meta.get("updated_at") or 0) < _PRICE_TTL
            ):
                payload = {
                    "price_usd": float(meta["last_price_usd"]),
                    "source": f"clickhouse_cache:{meta.get('last_price_source') or 'token_meta'}",
                    "timestamp": float(meta.get("updated_at") or time.time()),
                    "confidence": 0.5,
                }
                if meta.get("decimals") is not None:
                    payload["decimals"] = int(meta["decimals"])
                _price_mem[cache_key] = {**payload, "_cached_at": time.time()}
                return payload
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
            logger.debug("price_oracle token_meta cache read failed: %s", exc)

    return None


async def _cache_set_price(token: str, chain: str, payload: dict[str, Any]) -> None:
    """Write-through: in-process + Redis + ClickHouse token_meta."""
    cache_key = f"{chain}:{token}"
    _price_mem[cache_key] = {**payload, "_cached_at": time.time()}
    if len(_price_mem) > _MEM_MAX:
        _price_mem.pop(next(iter(_price_mem)), None)

    await _redis_set(f"price:{chain}:{token}", payload, ttl=_PRICE_TTL)

    try:
        meta = await _meta_get(token, chain)
        decimals = payload.get("decimals")
        if decimals is None and meta and meta.get("decimals") is not None:
            decimals = int(meta["decimals"])
        if decimals is not None:
            payload["decimals"] = decimals
        await _meta_upsert(
            token,
            chain,
            symbol=str(payload.get("symbol", "") or ""),
            price_usd=float(payload.get("price_usd", 0) or 0),
        )
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("price_oracle token_prices write-through failed: %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# TIER 1: OWN DATA — dex_events reserve ratios
# ═══════════════════════════════════════════════════════════════════════════


async def _own_dex_events(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Derive USD price from dex_events reserve ratios (free, 59M rows).

    Primary path: the spec's argMax query — the latest reserve ratio
    `amount1_out / amount0_in` per block for any indexed pair.

    Fallback path: VWAP across recent swaps via own_dex_data when
    token0/token1 columns are empty on historical rows.

    Raw ratios are decimals-normalized and anchored to USD using the
    paired token: stablecoin (~$1), wrapped native (x native USD price),
    or a one-level recursive paired-token price.
    """
    raw_price: float | None = None
    paired_token: str = ""
    swap_count = 0

    # Primary: argMax latest ratio (both directions)
    argmax = await _own_price_argmax(token, chain)
    if argmax and argmax.get("price") is not None and argmax.get("price", 0) > 0:
        raw_price = float(argmax["price"])
        paired_token = str(argmax.get("paired_token", "") or "")
        swap_count = int(argmax.get("swap_count", 0) or 0)

    # Fallback: VWAP from recent swaps
    if raw_price is None or not paired_token:
        vwap = await _own_price_vwap(token, chain)
        if vwap and vwap.get("price") is not None and vwap.get("price", 0) > 0:
            raw_price = float(vwap["price"])
            paired_token = str(vwap.get("paired_token", "") or "")
            swap_count = int(vwap.get("swap_count", 0) or 0)

    if raw_price is None or raw_price <= 0:
        return None

    # Decimals-normalize the raw ratio: raw = paired_raw / our_raw
    our_decimals = await get_token_decimals(token, chain)
    paired_decimals = await get_token_decimals(paired_token, chain)
    if our_decimals is None or paired_decimals is None:
        return None
    norm_price = raw_price * math.pow(10, our_decimals) / math.pow(10, paired_decimals)

    # Anchor to USD
    usd_price, confidence, anchor = await _anchor_to_usd(norm_price, paired_token, chain, _depth)

    if usd_price <= 0:
        return None

    source = "own_dex_events" if anchor != "unanchored" else "own_dex_events_unanchored"
    # Confidence grows with swap count (more evidence = more trust)
    evidence = min(1.0, swap_count / 50)
    confidence = max(0.05, min(1.0, confidence * (0.5 + 0.5 * evidence)))

    return {
        "price_usd": usd_price,
        "confidence": round(confidence, 4),
        "swap_count": swap_count,
        "paired_token": paired_token,
        "anchor": anchor,
        "source": source,
    }


async def _own_price_argmax(token: str, chain: str) -> dict[str, Any] | None:
    """Latest reserve ratio from dex_events (the spec's tier-1 SQL)."""

    def _query(side: str) -> list[tuple[Any, ...]]:
        client = _get_ch_client()
        if client is None:
            return []
        # side == "0": our token is token0, ratio = amount1_out/amount0_in
        # side == "1": our token is token1, ratio = amount0_out/amount1_in
        if side == "0":
            ratio_expr = "amount1_out / amount0_in"
            paired_col = "token1"
            where_extra = "token0 = %(token)s AND amount0_in > 0 AND amount1_out > 0"
        else:
            ratio_expr = "amount0_out / amount1_in"
            paired_col = "token0"
            where_extra = "token1 = %(token)s AND amount1_in > 0 AND amount0_out > 0"
        try:
            # ratio_expr/paired_col/where_extra are internal constants,
            # not user input; params are bound via clickhouse_driver placeholders.
            return list(
                client.execute(
                    f"""
                    SELECT
                        argMax({ratio_expr}, block_number) AS latest_ratio,
                        argMax({paired_col}, block_number) AS paired,
                        count() AS swaps,
                        uniq(contract_address) AS pools
                    FROM wallet_memory.dex_events
                    WHERE chain = %(chain)s
                      AND type = 'swap'
                      AND {where_extra}
                      AND block_timestamp >= now() - INTERVAL 24 HOUR
                    """,
                    {"token": token, "chain": chain},
                )
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("price_oracle argMax query failed (%s): %s", side, exc)
            return []

    for side in ("0", "1"):
        rows = await run_sync(_query, side)
        if not rows or not rows[0]:
            continue
        ratio, paired, swaps, pools = rows[0]
        if ratio is None or float(ratio or 0) <= 0 or not paired:
            continue
        return {
            "price": float(ratio),
            "paired_token": str(paired),
            "swap_count": int(swaps or 0),
            "pools": int(pools or 0),
        }
    return None


async def _own_price_vwap(token: str, chain: str) -> dict[str, Any] | None:
    """VWAP price fallback over recent swaps (handles empty token0/token1)."""
    try:
        from app.domains.scanner.engines.own_dex_data.client import _load_pair_cache
        from app.domains.scanner.engines.own_dex_data.query import _query_dex_swaps

        swaps = await _query_dex_swaps(token, chain, hours_back=24)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("price_oracle vwap swap query failed: %s", exc)
        return None
    if len(swaps) < 2:
        return None

    pair_cache = _load_pair_cache(chain)
    prices_weighted: list[tuple[float, float]] = []
    paired_counts: dict[str, int] = {}

    for swap in swaps:
        t0 = str(swap.get("token0") or "").lower()
        t1 = str(swap.get("token1") or "").lower()
        if not t0 or not t1:
            cached = pair_cache.get(str(swap.get("contract_address") or "").lower())
            if cached:
                t0, t1 = (cached[0].lower(), cached[1].lower())
        if not t0 or not t1:
            continue

        is_token0 = t0 == token.lower()
        paired = t1 if is_token0 else t0
        if not paired:
            continue
        paired_counts[paired] = paired_counts.get(paired, 0) + 1

        try:
            if is_token0:
                amt_in = float(swap.get("amount0_in") or 0)
                amt_out = float(swap.get("amount0_out") or 0)
                paired_in = float(swap.get("amount1_in") or 0)
                paired_out = float(swap.get("amount1_out") or 0)
            else:
                amt_in = float(swap.get("amount1_in") or 0)
                amt_out = float(swap.get("amount1_out") or 0)
                paired_in = float(swap.get("amount0_in") or 0)
                paired_out = float(swap.get("amount0_out") or 0)
        except (ValueError, TypeError):
            continue

        weight = amt_in + amt_out
        if weight <= 0:
            continue
        # buy: our token out, paired in; sell: our token in, paired out
        if amt_out > 0 and paired_in > 0:
            price = paired_in / amt_out
        elif amt_in > 0 and paired_out > 0:
            price = paired_out / amt_in
        else:
            continue
        if 0 < price < 1_000_000_000:
            prices_weighted.append((price, weight))

    if not prices_weighted:
        return None

    total_weight = sum(w for _, w in prices_weighted)
    vwap = sum(p * w for p, w in prices_weighted) / total_weight if total_weight > 0 else 0.0
    paired_token = max(paired_counts, key=lambda k: paired_counts[k]) if paired_counts else ""

    return {
        "price": vwap,
        "paired_token": paired_token,
        "swap_count": len(prices_weighted),
    }


async def _anchor_to_usd(
    norm_price: float,
    paired_token: str,
    chain: str,
    depth: int,
) -> tuple[float, float, str]:
    """Convert a decimals-normalized pair price to USD.

    Returns (usd_price, confidence, anchor_kind):
      - stablecoin pair       -> anchor ~$1.00
      - wrapped native pair   -> x native price
      - other paired token    -> x paired token USD price (one recursion level)
      - unresolvable          -> unanchored value with low confidence
    """
    paired = _normalize_token(paired_token, chain)

    if paired in _STABLECOINS:
        return norm_price, 0.85, "stablecoin"

    if _WRAPPED_NATIVE.get(chain, "").lower() == paired.lower():
        native_price = await get_native_price(chain)
        if native_price > 0:
            return norm_price * native_price, 0.75, "native"
        return 0.0, 0.0, "native_unpriced"

    if depth < 1:
        try:
            paired_price = await get_token_price(paired, chain, _depth=depth + 1)
            if paired_price and float(paired_price.get("price_usd", 0) or 0) > 0:
                return norm_price * float(paired_price["price_usd"]), 0.6, "paired_token"
        except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError) as exc:
            logger.debug("price_oracle paired anchor failed for %s: %s", paired[:16], exc)

    # Last resort: return the normalized ratio flagged as unanchored
    return norm_price, 0.15, "unanchored"


# ── Ordered fallback chain (tier order per spec) ───────────────────────────
_TIERS: list[tuple[str, Callable[..., Any]]] = [
    ("own_dex_events", _own_dex_events),
    ("dexscreener", _dexscreener_price),
    ("coingecko", _coingecko_price),
    ("defillama", _defillama_price),
    ("geckoterminal", _geckoterminal_price),
    ("paraswap", _paraswap_price),
    ("1inch", _oneinch_price),
]


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING / TESTING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return _common_get_cache_stats(
        extra={
            "price_cache_size": len(_price_mem),
            "price_cache_max": _MEM_MAX,
            "decimals_cache_size": len(_decimals_mem),
            "native_cache_size": len(_native_mem),
            "clickhouse_connected": _ch_client is not None,
            "redis_connected": _redis_client is not None,
        },
    )


def clear_cache() -> None:
    """Clear all in-process caches (for testing / memory pressure)."""
    _common_clear_cache(_price_mem, _decimals_mem, _native_mem)
