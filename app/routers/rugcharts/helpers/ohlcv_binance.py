"""Binance public klines adapter — keyless candle history for major assets.

Hosts are tried in order because ``api.binance.com`` geo-blocks some regions
(HTTP 451); ``data-api.binance.vision`` is Binance's public market-data mirror
and serves the same klines with no key.
"""

from __future__ import annotations

from typing import Any

from .ohlcv_schema import normalize_candles
from .ohlcv_source_http import (
    REASON_EMPTY,
    REASON_GEO_BLOCKED,
    REASON_NETWORK,
    REASON_UNSUPPORTED,
    fetch_json,
)

BINANCE_HOSTS = (
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api.binance.us",
)

# Majors only: a Binance kline is an ASSET price, never a pool quote, so it may
# only stand in when the pair's base asset is the canonical major.
BINANCE_SYMBOLS = {
    "solana": {
        "so11111111111111111111111111111111111111112": "SOLUSDT",
        "epjfwdd5aufqssqem2qn1xzybapc8g4wegkzwytdt1v": "USDCUSDT",
    },
    "ethereum": {
        "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "ETHUSDT",
        "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599": "BTCUSDT",
        "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDCUSDT",
    },
    "base": {
        "0x4200000000000000000000000000000000000006": "ETHUSDT",
        "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": "BTCUSDT",
        "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDCUSDT",
    },
}

BINANCE_INTERVALS: dict[str, tuple[str, int | None]] = {
    "1m": ("1m", None),
    "5m": ("5m", None),
    "15m": ("15m", None),
    "1h": ("1h", None),
    "4h": ("4h", None),
    "1d": ("1d", None),
    "7d": ("1h", 168),
    "30d": ("1d", 30),
}

_REJECT = (REASON_GEO_BLOCKED, REASON_NETWORK)


def _resolve_symbol(chain: str, asset: str) -> str | None:
    """Map an address on ``chain`` (or a bare ticker) to a Binance symbol."""
    cleaned = (asset or "").strip().lower()
    if not cleaned:
        return None
    mapped = BINANCE_SYMBOLS.get(chain, {}).get(cleaned)
    if mapped:
        return mapped
    if cleaned.isalpha() and 2 <= len(cleaned) <= 10:
        return f"{cleaned.upper()}USDT"
    return None


def _rows_to_candles(payload: Any) -> list[dict[str, Any]]:
    """Binance kline arrays to candles (open time is milliseconds UTC)."""
    if not isinstance(payload, list):
        return []
    return normalize_candles(
        [
            [int(row[0]) // 1000, row[1], row[2], row[3], row[4], row[5]]
            for row in payload
            if isinstance(row, list) and len(row) >= 6
        ]
    )


async def fetch_ohlcv_binance(
    chain: str, asset: str, timeframe: str = "1h", limit: int = 100
) -> tuple[list[dict[str, Any]], str | None]:
    """Fetch asset-scoped candles from the first Binance host that answers."""
    symbol = _resolve_symbol(chain, asset)
    if not symbol:
        return [], REASON_UNSUPPORTED
    interval, forced_limit = BINANCE_INTERVALS.get(timeframe, ("1h", None))
    reason: str | None = REASON_UNSUPPORTED
    for host in BINANCE_HOSTS:
        payload, reason = await fetch_json(
            f"{host}/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": min(forced_limit or limit, 1000)},
        )
        if reason in _REJECT:
            continue
        if reason is None:
            candles = _rows_to_candles(payload)
            return (candles, None) if candles else ([], REASON_EMPTY)
        break
    return [], reason
