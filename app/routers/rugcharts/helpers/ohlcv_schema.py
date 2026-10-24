"""Canonical OHLCV candle schema shared by every RugCharts chart source."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise
from typing import Any

from .common import logger

CANDLE_KEYS = ("time", "open", "high", "low", "close", "volume")

# Bucket width per timeframe in seconds — used to spot a coarse feed.
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "7d": 86400,
    "30d": 86400,
}

# What the price actually is: a pool quote (token pair) or the canonical asset.
SOURCE_SCOPE = {
    "clickhouse": "pool",
    "geckoterminal": "pool",
    "dexscreener": "pool",
    "binance": "asset",
    "coingecko": "asset",
}


def _to_candle(row: Any) -> dict[str, Any] | None:
    """Coerce one dict/sequence row into the canonical candle or drop it."""
    try:
        if isinstance(row, dict):
            raw = [row.get(key) for key in CANDLE_KEYS]
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
            raw = list(row)[:6]
        else:
            return None
        if len(raw) < 6:
            return None
        time, open_, high, low, close = (float(value) for value in raw[:5])
        volume = float(raw[5] or 0.0)
    except (TypeError, ValueError) as exc:
        logger.debug("dropping malformed candle row: %s", str(exc)[:80])
        return None
    if time <= 0 or min(open_, high, low, close) <= 0 or high < low or volume < 0:
        return None
    return {
        "time": int(time),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def normalize_candles(rows: Any) -> list[dict[str, Any]]:
    """Normalize any source's rows: ascending time, unique, valid OHLCV only."""
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    by_time: dict[int, dict[str, Any]] = {}
    for row in rows:
        candle = _to_candle(row)
        if candle is not None:
            by_time[candle["time"]] = candle
    return [by_time[ts] for ts in sorted(by_time)]


def is_coarse(candles: list[dict[str, Any]], timeframe: str) -> bool:
    """True when the feed's bucket is wider than the requested timeframe."""
    expected = TIMEFRAME_SECONDS.get(timeframe)
    if not expected or len(candles) < 2:
        return False
    deltas = [later["time"] - earlier["time"] for earlier, later in pairwise(candles)]
    median = sorted(deltas)[len(deltas) // 2]
    return median > expected * 1.5
