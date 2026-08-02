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
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Cache TTLs ─────────────────────────────────────────────────────────────
_PRICE_TTL = 300  # Redis + token_meta price cache TTL (seconds)
_MEM_TTL = 60  # in-process price cache TTL (seconds)
_MEM_MAX = 500  # in-process cache max entries (LRU eviction)
_NATIVE_TTL = 300  # native price cache TTL (seconds)

# ── Chain normalization ────────────────────────────────────────────────────
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
    "linea": "linea",
    "zksync": "zksync",
    "gnosis": "gnosis",
    "xdai": "gnosis",
    "scroll": "scroll",
    "mantle": "mantle",
    "solana": "solana",
}

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

# ── Native coin IDs for CoinGecko simple/price fallback (no key required) ──
_NATIVE_COINGECKO_IDS: dict[str, str] = {
    "ethereum": "ethereum",
    "bsc": "binancecoin",
    "polygon": "matic-network",
    "arbitrum": "ethereum",
    "optimism": "ethereum",
    "base": "ethereum",
    "avalanche": "avalanche-2",
    "fantom": "fantom",
    "linea": "ethereum",
    "gnosis": "xdai",
    "mantle": "mantle",
    "solana": "solana",
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
        from clickhouse_driver.errors import Error as CHDriverError

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


async def _run_sync(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a sync function in a thread pool (clickhouse_driver is sync)."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def ensure_token_meta_table() -> bool:
    """Create the ClickHouse token_meta table if it does not exist.

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
                CREATE TABLE IF NOT EXISTS wallet_memory.token_meta (
                    token_address String,
                    chain LowCardinality(String),
                    symbol String DEFAULT '',
                    name String DEFAULT '',
                    decimals Nullable(UInt8),
                    last_price_usd Nullable(Float64),
                    last_price_source String DEFAULT '',
                    updated_at DateTime DEFAULT now()
                ) ENGINE = ReplacingMergeTree()
                ORDER BY (chain, token_address)
                """
            )
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("token_meta ensure failed: %s", exc)
            return False

    return bool(await _run_sync(_ensure))


async def _meta_upsert(
    token_address: str,
    chain: str,
    *,
    decimals: int | None = None,
    symbol: str = "",
    name: str = "",
    price_usd: float | None = None,
    source: str = "",
) -> None:
    """Upsert a token_meta row (ReplacingMergeTree replaces on merge)."""

    def _write() -> None:
        client = _get_ch_client()
        if client is None:
            return
        try:
            client.execute(
                """
                INSERT INTO wallet_memory.token_meta
                    (token_address, chain, symbol, name, decimals,
                     last_price_usd, last_price_source, updated_at)
                VALUES
                """,
                [(token_address, chain, symbol, name, decimals, price_usd, source, time.time())],
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("token_meta upsert failed for %s: %s", token_address[:16], exc)

    await _run_sync(_write)


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

    rows = await _run_sync(_read)
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
    except Exception as exc:
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
    except Exception as exc:
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
        except Exception as exc:  # defensive: a provider must never crash the oracle
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
    except Exception as exc:
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
    except Exception as exc:
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
        except Exception as exc:
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
    except Exception as exc:
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
        except Exception as exc:
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
        await _meta_upsert(
            token,
            chain,
            decimals=decimals,
            symbol=str(payload.get("symbol", "") or ""),
            name=str(payload.get("name", "") or ""),
            price_usd=float(payload.get("price_usd", 0) or 0),
            source=str(payload.get("source", "") or ""),
        )
    except Exception as exc:
        logger.debug("price_oracle token_meta write-through failed: %s", exc)


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
        rows = await _run_sync(_query, side)
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
    except Exception as exc:
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
        except Exception as exc:
            logger.debug("price_oracle paired anchor failed for %s: %s", paired[:16], exc)

    # Last resort: return the normalized ratio flagged as unanchored
    return norm_price, 0.15, "unanchored"


# ═══════════════════════════════════════════════════════════════════════════
# FREE API TIERS (reuse verified databus implementations where they exist)
# ═══════════════════════════════════════════════════════════════════════════


async def _dexscreener_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 2 — DexScreener token price (free, 300 req/min, no auth)."""
    try:
        from app.domains.databus.providers.chains.dexscreener import _dexscreener_price as _ds

        raw = await _ds(mint=token, token=token)
        if not raw:
            return None
        pairs = raw.get("pairs") if isinstance(raw, dict) else None
        if not pairs:
            return None

        best_pair: dict[str, Any] | None = None
        best_liquidity = -1.0
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            liq = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            if liq > best_liquidity:
                best_liquidity = liq
                best_pair = pair
        if best_pair is None:
            return None

        price_usd = _pair_price_for_token(best_pair, token)
        if price_usd <= 0:
            return None

        result: dict[str, Any] = {
            "price_usd": price_usd,
            "confidence": 0.7,
            "volume_24h_usd": float(best_pair.get("volume", {}).get("h24", 0) or 0),
            "market_cap_usd": float(best_pair.get("fdv", 0) or 0) or None,
            "price_change_24h_pct": float(best_pair.get("priceChange", {}).get("h24", 0) or 0),
        }
        decimals = _pair_decimals_for_token(best_pair, token)
        if decimals is not None:
            result["decimals"] = decimals
        base = best_pair.get("baseToken") or {}
        if isinstance(base, dict) and base.get("symbol"):
            result["symbol"] = str(base.get("symbol") or "")
        if isinstance(base, dict) and base.get("name"):
            result["name"] = str(base.get("name") or "")
        return result
    except Exception as exc:
        logger.debug("price_oracle DexScreener failed for %s: %s", token[:16], exc)
        return None


def _pair_price_for_token(pair: dict[str, Any], token: str) -> float:
    """Extract the USD price of `token` from a DexScreener pair object.

    DexScreener semantics: priceUsd = USD per 1 base token; priceNative =
    quote units per 1 base token. When our token is the quote side, the
    USD price is priceUsd / priceNative.
    """
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    if not isinstance(base, dict) or not isinstance(quote, dict):
        return 0.0

    base_addr = str(base.get("address") or "").lower()
    quote_addr = str(quote.get("address") or "").lower()
    token_addr = str(token).lower()

    try:
        price_usd = float(pair.get("priceUsd") or 0)
        price_native = float(pair.get("priceNative") or 0)
    except (ValueError, TypeError):
        return 0.0

    if base_addr == token_addr:
        return price_usd
    if quote_addr == token_addr and price_native > 0:
        return price_usd / price_native
    return 0.0


def _pair_decimals_for_token(pair: dict[str, Any], token: str) -> int | None:
    """Return the decimals of `token` from a DexScreener pair (if present)."""
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    token_addr = str(token).lower()
    for side in (base, quote):
        if not isinstance(side, dict):
            continue
        if str(side.get("address") or "").lower() == token_addr and side.get("decimals") is not None:
            try:
                return int(side["decimals"])
            except (ValueError, TypeError):
                return None
    return None


async def _decimals_from_dexscreener(token: str, chain: str) -> int | None:
    """Best-effort decimals resolution from a DexScreener pair response."""
    price_result = await _dexscreener_price(token, chain)
    if price_result and price_result.get("decimals") is not None:
        return int(price_result["decimals"])
    return None


async def _coingecko_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 3 — CoinGecko simple/token_price (free, 10-30 req/min).

    Reuses the verified free_security provider (same endpoint, no auth).
    """
    try:
        from app.domains.databus.providers.chains.free_security import _free_market_coingecko

        return await _free_market_coingecko(token_address=token, chain=chain, address=token)
    except Exception as exc:
        logger.debug("price_oracle CoinGecko failed for %s: %s", token[:16], exc)
        return None


async def _defillama_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 4 — DeFiLlama current price (free, no auth).

    https://coins.llama.fi/prices/current/{chain}:{address}
    """
    prefix_map = {
        "ethereum": "ethereum",
        "bsc": "bsc",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "base": "base",
        "avalanche": "avalanche",
        "fantom": "fantom",
        "linea": "linea",
        "gnosis": "xdai",
        "mantle": "mantle",
        "solana": "solana",
    }
    prefix = prefix_map.get(chain)
    if not prefix:
        return None

    try:
        import httpx

        llama_id = f"{prefix}:{token}"
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"https://coins.llama.fi/prices/current/{llama_id}")
            if resp.status_code != 200:
                return None
            data = resp.json()
            coins = data.get("coins", {}) if isinstance(data, dict) else {}
            coin_data = coins.get(llama_id) or coins.get(token)
            if not isinstance(coin_data, dict):
                return None
            price = float(coin_data.get("price", 0) or 0)
            if price <= 0:
                return None
            result: dict[str, Any] = {
                "price_usd": price,
                "confidence": float(coin_data.get("confidence", 0.5) or 0.5),
            }
            if coin_data.get("volume24h") is not None:
                result["volume_24h_usd"] = float(coin_data.get("volume24h") or 0)
            if coin_data.get("marketCap") is not None:
                result["market_cap_usd"] = float(coin_data.get("marketCap") or 0)
            return result
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError, httpx.HTTPError) as exc:
        logger.debug("price_oracle DeFiLlama failed for %s: %s", token[:16], exc)
        return None


async def _geckoterminal_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 5 — GeckoTerminal token price (free, no auth).

    Reuses the verified free_security provider (same endpoint).
    """
    try:
        from app.domains.databus.providers.chains.free_security import _free_market_geckoterminal

        return await _free_market_geckoterminal(token_address=token, chain=chain, address=token)
    except Exception as exc:
        logger.debug("price_oracle GeckoTerminal failed for %s: %s", token[:16], exc)
        return None


async def _paraswap_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 6a — ParaSwap price quote (free, no auth).

    Reuses the verified free_security provider which sells 1 native unit
    for the token; the resulting token-per-native ratio is converted to
    USD via get_native_price.
    """
    try:
        from app.domains.databus.providers.chains.free_security import _free_market_paraswap_quote

        result = await _free_market_paraswap_quote(token_address=token, chain=chain, address=token)
        if not result:
            return None
        src_amount = float(result.get("src_amount", 0) or 0)
        dest_amount = float(result.get("dest_amount", 0) or 0)
        if src_amount <= 0 or dest_amount <= 0:
            return None
        native_price = await get_native_price(chain)
        if native_price <= 0:
            return None
        # src = native amount, dest = token amount for 1 native
        # token_price_usd = (native_per_token) * native_usd
        token_price_usd = (src_amount / dest_amount) * native_price
        return {"price_usd": token_price_usd, "confidence": 0.55}
    except Exception as exc:
        logger.debug("price_oracle ParaSwap failed for %s: %s", token[:16], exc)
        return None


async def _oneinch_price(token: str, chain: str, *, _depth: int = 0) -> dict[str, Any] | None:
    """Tier 6b — 1inch quote (FREEMIUM; only fires when ONEINCH_API_KEY is set)."""
    api_key = os.getenv("ONEINCH_API_KEY", "")
    if not api_key:
        return None
    try:
        from app.domains.databus.providers.chains.free_security import _free_market_oneinch_quote

        result = await _free_market_oneinch_quote(
            token_address=token,
            chain=chain,
            address=token,
            api_key=api_key,
        )
        if not result:
            return None
        src_amount = float(result.get("src_amount", 0) or 0)
        dest_amount = float(result.get("dest_amount", 0) or 0)
        if src_amount <= 0 or dest_amount <= 0:
            return None
        native_price = await get_native_price(chain)
        if native_price <= 0:
            return None
        token_price_usd = (src_amount / dest_amount) * native_price
        return {"price_usd": token_price_usd, "confidence": 0.5}
    except Exception as exc:
        logger.debug("price_oracle 1inch failed for %s: %s", token[:16], exc)
        return None


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
# NATIVE PRICE FALLBACKS
# ═══════════════════════════════════════════════════════════════════════════


async def _native_coingecko(chain: str) -> float:
    """CoinGecko simple/price by native coin id (free, no auth)."""
    coin_id = _NATIVE_COINGECKO_IDS.get(chain)
    if not coin_id:
        return 0.0
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": coin_id, "vs_currencies": "usd"},
            )
            if resp.status_code != 200:
                return 0.0
            data = resp.json()
            coin = data.get(coin_id) if isinstance(data, dict) else None
            if isinstance(coin, dict):
                return float(coin.get("usd", 0) or 0)
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError, httpx.HTTPError) as exc:
        logger.debug("price_oracle native CoinGecko failed for %s: %s", chain, exc)
    return 0.0


async def _native_defillama(chain: str) -> float:
    """DeFiLlama native coin price by chain id (free, no auth)."""
    prefix = {
        "ethereum": "ethereum",
        "bsc": "bsc",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "base": "base",
        "avalanche": "avalanche",
        "fantom": "fantom",
        "solana": "solana",
    }.get(chain)
    if not prefix:
        return 0.0
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"https://coins.llama.fi/prices/current/{prefix}")
            if resp.status_code != 200:
                return 0.0
            data = resp.json()
            coins = data.get("coins", {}) if isinstance(data, dict) else {}
            coin = coins.get(prefix) or coins.get(f"coingecko:{prefix}")
            if isinstance(coin, dict):
                return float(coin.get("price", 0) or 0)
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, ImportError, httpx.HTTPError) as exc:
        logger.debug("price_oracle native DeFiLlama failed for %s: %s", chain, exc)
    return 0.0


# ═══════════════════════════════════════════════════════════════════════════
# MONITORING / TESTING
# ═══════════════════════════════════════════════════════════════════════════


def get_cache_stats() -> dict[str, Any]:
    """Return cache statistics for monitoring."""
    return {
        "price_cache_size": len(_price_mem),
        "price_cache_max": _MEM_MAX,
        "decimals_cache_size": len(_decimals_mem),
        "native_cache_size": len(_native_mem),
        "clickhouse_connected": _ch_client is not None,
        "redis_connected": _redis_client is not None,
    }


def clear_cache() -> None:
    """Clear all in-process caches (for testing / memory pressure)."""
    _price_mem.clear()
    _decimals_mem.clear()
    _native_mem.clear()
