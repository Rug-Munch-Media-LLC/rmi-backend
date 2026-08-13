"""Candle-level TA summaries for the RugMunch chat bot.

``ta_summary`` turns a list of OHLCV candle dicts (the shape produced by
``app.databus.ohlcv_engine``: ``open/high/low/close/volume/timestamp``) into a
single structured verdict dict, and ``ta_summary_text`` renders it as a
concise human/LLM-friendly line.

All extraction is defensive: missing / non-numeric / NaN / inf fields are
skipped, and every indicator degrades to ``None`` when there is not enough
data. This module never raises on malformed candles.
"""

from __future__ import annotations

import math
from typing import Any

from app.domains.ta.indicators import (
    atr,
    macd,
    pct_change,
    rsi,
    sma,
    support_resistance,
    trend,
)

__all__ = ["ta_summary", "ta_summary_text"]

# 24 candles ≈ 24h on 1h candles, 1 day on 1d candles, etc.
CHANGE_WINDOW = 24


def _extract(candles: list[dict[str, Any]], key: str) -> list[float]:
    """Pull a numeric field out of candle dicts, skipping bad values."""
    values: list[float] = []
    for candle in candles:
        try:
            value = candle.get(key)
            if value is None:
                continue
            value = float(value)
            if math.isnan(value) or math.isinf(value):
                continue
            values.append(value)
        except (AttributeError, TypeError, ValueError):
            continue
    return values


def _macd_verdict(macd_result: dict[str, float | None]) -> str:
    """Short human verdict for a MACD result dict."""
    macd_value = macd_result.get("macd")
    histogram = macd_result.get("histogram")
    if macd_value is None:
        return "n/a"
    if histogram is None:
        return "bullish" if macd_value > 0 else "bearish" if macd_value < 0 else "flat"
    if macd_value > 0 and histogram > 0:
        return "bullish"
    if macd_value < 0 and histogram < 0:
        return "bearish"
    return "mixed"


def _signal(
    price: float | None,
    rsi_value: float | None,
    macd_result: dict[str, float | None],
    trend_value: str,
    sma_50: float | None,
) -> str:
    """Rule-of-thumb bullish/bearish/neutral verdict.

    Scores +1/-1 per aligned indicator (RSI, MACD level, MACD momentum,
    trend, price vs 50-SMA) and returns:
        - "bullish" when the net score is >= 2
        - "bearish" when the net score is <= -2
        - "neutral" otherwise (including when most inputs are unavailable)
    """
    score = 0.0

    if rsi_value is not None:
        if rsi_value > 55:
            score += 1
        elif rsi_value < 45:
            score -= 1

    macd_value = macd_result.get("macd")
    histogram = macd_result.get("histogram")
    if macd_value is not None:
        score += 1 if macd_value > 0 else -1 if macd_value < 0 else 0
        if histogram is not None:
            score += 0.5 if histogram > 0 else -0.5 if histogram < 0 else 0

    if trend_value == "uptrend":
        score += 1
    elif trend_value == "downtrend":
        score -= 1

    if price is not None and sma_50 is not None:
        score += 1 if price > sma_50 else -1

    if score >= 2:
        return "bullish"
    if score <= -2:
        return "bearish"
    return "neutral"


def ta_summary(
    candles: list[dict[str, Any]],
    symbol: str | None = None,
    timeframe: str | None = None,
) -> dict[str, Any]:
    """Compute a structured TA summary from OHLCV candle dicts.

    Returns a dict with keys: ``symbol``, ``timeframe``, ``last_price``,
    ``rsi``, ``macd`` (the raw dict), ``sma_20``, ``sma_50``, ``sma_200``,
    ``atr``, ``support``, ``resistance``, ``trend``, ``change_24h`` and
    ``signal``. Indicator values are ``None`` when the candle series is too
    short; the dict itself is always fully populated.
    """
    closes = _extract(candles, "close")
    highs = _extract(candles, "high")
    lows = _extract(candles, "low")

    meta = candles[0] if candles else {}
    resolved_symbol = symbol or meta.get("symbol") or meta.get("token") or "unknown"
    resolved_timeframe = timeframe or meta.get("timeframe") or "1h"

    last_price = closes[-1] if closes else None
    rsi_value = rsi(closes) if closes else None
    empty_macd: dict[str, float | None] = {"macd": None, "signal": None, "histogram": None}
    macd_result = macd(closes) if closes else empty_macd
    trend_value = trend(closes) if closes else "sideways"

    change_window = closes[-CHANGE_WINDOW:] if closes else []
    change_24h = pct_change(change_window) if len(change_window) >= 2 else None

    sr = support_resistance(highs, lows) if highs and lows else {"support": None, "resistance": None}

    sma_50_value = sma(closes, 50) if closes else None

    return {
        "symbol": resolved_symbol,
        "timeframe": resolved_timeframe,
        "last_price": last_price,
        "rsi": rsi_value,
        "macd": macd_result,
        "sma_20": sma(closes, 20) if closes else None,
        "sma_50": sma_50_value,
        "sma_200": sma(closes, 200) if closes else None,
        "atr": atr(highs, lows, closes) if highs and lows and closes else None,
        "support": sr["support"],
        "resistance": sr["resistance"],
        "trend": trend_value,
        "change_24h": change_24h,
        "signal": _signal(last_price, rsi_value, macd_result, trend_value, sma_50_value),
    }


def _fmt_price(value: float | None) -> str:
    """Format a price to <= 2 decimals, keeping micro-prices readable."""
    if value is None:
        return "n/a"
    if value == 0:
        return "0.00"
    if abs(value) >= 0.01:
        return f"{value:.2f}"
    # Micro-cap prices: 2 decimals would render "0.00" — show significant digits.
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _fmt_rsi(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}"


def _rsi_zone(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 70:
        return "overbought"
    if value <= 30:
        return "oversold"
    return "neutral"


def ta_summary_text(summary: dict[str, Any]) -> str:
    """Render a ``ta_summary`` dict as a concise text block.

    Example::

        RSI 62.0 (neutral) · MACD bullish · above 50-SMA · support $1.23 /
        resistance $1.45 · uptrend · signal bullish
    """
    parts: list[str] = []

    rsi_value = summary.get("rsi")
    parts.append(f"RSI {_fmt_rsi(rsi_value)} ({_rsi_zone(rsi_value)})")

    macd_result = summary.get("macd") or {}
    parts.append(f"MACD {_macd_verdict(macd_result)}")

    last_price = summary.get("last_price")
    sma_50 = summary.get("sma_50")
    if last_price is not None and sma_50 is not None:
        parts.append(f"{'above' if last_price > sma_50 else 'below'} 50-SMA")

    support = summary.get("support")
    resistance = summary.get("resistance")
    if support is not None and resistance is not None:
        parts.append(f"support ${_fmt_price(support)} / resistance ${_fmt_price(resistance)}")

    parts.append(str(summary.get("trend", "sideways")))

    signal = summary.get("signal")
    if signal:
        parts.append(f"signal {signal}")

    return " · ".join(parts)
