"""Per-chain OHLCV fallback ladder with provenance, caching and null+reason.

Order (first source that returns candles wins):

1. ``clickhouse``   — own chain-indexer MV, pool-scoped, freshest on-chain data
2. ``geckoterminal``— free keyless OHLCV per pool, pool-scoped (primary live)
3. ``binance``      — free keyless klines, asset-scoped, majors only
4. ``coingecko``    — free demo OHLC, asset-scoped, rate-limited, coarse buckets
5. ``dexscreener``  — free keyless live pair snapshot, pool-scoped, ONE candle

Nothing paid, nothing scraped: every source is a public JSON API. When all of
them fail the caller gets ``[]`` plus a ``reason`` naming why.

Every adapter is wrapped to one call shape ``(pair, chain, timeframe, limit)``
so the ladder never has to know a source's own argument order.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .common import OHLCV_TTL, _cache_get_json, _cache_set_json, logger
from .ohlcv import _fetch_ohlcv_clickhouse, _fetch_ohlcv_gecko_reason
from .ohlcv_asset import resolve_base_asset
from .ohlcv_binance import fetch_ohlcv_binance
from .ohlcv_coingecko import fetch_ohlcv_coingecko
from .ohlcv_dexscreener import fetch_ohlcv_dexscreener_snapshot
from .ohlcv_schema import SOURCE_SCOPE, is_coarse
from .ohlcv_source_http import REASON_EMPTY

CACHE_PREFIX = "rugcharts:ohlcv:v2"
MISS_TTL = 30  # negative cache: do not hammer a rate-limited source

SourceFn = Callable[..., Awaitable[tuple[list[dict[str, Any]], str | None]]]

_EVM_LADDER = ("clickhouse", "geckoterminal", "binance", "coingecko", "dexscreener")

# Chains without a Binance/CoinGecko mapping still get the pool-scoped tail.
CHAIN_LADDERS: dict[str, tuple[str, ...]] = {
    "solana": _EVM_LADDER,
    "ethereum": _EVM_LADDER,
    "base": _EVM_LADDER,
    "bsc": _EVM_LADDER,
    "arbitrum": _EVM_LADDER,
    "polygon": _EVM_LADDER,
    "optimism": _EVM_LADDER,
    "avalanche": _EVM_LADDER,
    "tron": _EVM_LADDER,
}
DEFAULT_LADDER = ("clickhouse", "geckoterminal", "dexscreener")

_PARSE_ERRORS = (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError)


async def _clickhouse(
    pair_address: str, chain: str, timeframe: str, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    """Own indexer candles; the MV has no reason channel, empty means absent."""
    return await _fetch_ohlcv_clickhouse(pair_address, chain, timeframe, limit), None


async def _gecko(pair_address: str, chain: str, timeframe: str, limit: int) -> tuple[list[dict[str, Any]], str | None]:
    """GeckoTerminal pool candles; carries the real reason (429 -> rate_limited)."""
    return await _fetch_ohlcv_gecko_reason(pair_address, chain, timeframe, limit)


async def _binance(
    pair_address: str, chain: str, timeframe: str, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    """Binance asset klines for the pool's base token."""
    asset = await resolve_base_asset(pair_address, chain)
    return await fetch_ohlcv_binance(chain, asset, timeframe, limit)


async def _coingecko(
    pair_address: str, chain: str, timeframe: str, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    """CoinGecko asset OHLC for the pool's base token."""
    asset = await resolve_base_asset(pair_address, chain)
    return await fetch_ohlcv_coingecko(chain, asset, timeframe, limit)


async def _dexscreener(
    pair_address: str, chain: str, timeframe: str, limit: int
) -> tuple[list[dict[str, Any]], str | None]:
    """DexScreener live snapshot — one degraded candle, no history."""
    return await fetch_ohlcv_dexscreener_snapshot(chain, pair_address)


SOURCE_FUNCS: dict[str, SourceFn] = {
    "clickhouse": _clickhouse,
    "geckoterminal": _gecko,
    "binance": _binance,
    "coingecko": _coingecko,
    "dexscreener": _dexscreener,
}


def _build_meta(
    chain: str,
    timeframe: str,
    candles: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    source: str | None,
) -> dict[str, Any]:
    """Describe exactly what served these candles and what was tried."""
    reasons = [a["reason"] for a in attempts if a.get("reason")]
    return {
        "source": source,
        "scope": SOURCE_SCOPE.get(source or "", "unknown"),
        "rows": len(candles),
        "chain": chain,
        "timeframe": timeframe,
        "degraded": source == "dexscreener" or is_coarse(candles, timeframe),
        "reason": None if candles else (reasons[-1] if reasons else REASON_EMPTY),
        "attempts": attempts,
    }


def _cached_candles(cached: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the candle list out of a cache envelope."""
    candles = cached.get("candles")
    return candles if isinstance(candles, list) else []


async def fetch_ohlcv(
    pair_address: str,
    chain: str,
    timeframe: str = "1h",
    limit: int = 100,
    sources: dict[str, SourceFn] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve candles for a pool through the per-chain ladder.

    Returns ``(candles, meta)``. ``meta.source`` is None when nothing served,
    ``meta.reason`` then says why (never a bare null). ``sources`` is a DI seam
    for tests; production always uses ``SOURCE_FUNCS``.
    """
    registry = sources or SOURCE_FUNCS
    cache_key = f"{CACHE_PREFIX}:{chain}:{pair_address}:{timeframe}:{limit}"
    if sources is None:
        cached = await _cache_get_json(cache_key)
        if cached:
            meta = dict(cached.get("meta") or {})
            meta["cached"] = True
            return _cached_candles(cached), meta

    attempts: list[dict[str, Any]] = []
    for source in CHAIN_LADDERS.get(chain, DEFAULT_LADDER):
        func = registry.get(source)
        if func is None:
            attempts.append({"source": source, "reason": "source_unavailable", "rows": 0})
            continue
        try:
            candles, reason = await func(pair_address, chain, timeframe, limit)
        except _PARSE_ERRORS as exc:
            logger.warning("chart source %s failed for %s: %s", source, pair_address, str(exc)[:120])
            candles, reason = [], "source_error"
        if not candles and reason is None:
            reason = REASON_EMPTY
        attempts.append({"source": source, "reason": reason, "rows": len(candles)})
        if candles:
            meta = _build_meta(chain, timeframe, candles, attempts, source)
            if sources is None:
                await _cache_set_json(cache_key, {"candles": candles, "meta": meta}, OHLCV_TTL)
            return candles, meta

    meta = _build_meta(chain, timeframe, [], attempts, None)
    if sources is None:
        await _cache_set_json(cache_key, {"candles": [], "meta": meta}, MISS_TTL)
    return [], meta
