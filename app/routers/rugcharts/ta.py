"""Shared technical-analysis indicators for RugCharts.

Single source of truth — previously RSI/MACD/Bollinger/SMA were implemented
three times independently (router /intel, router /ta, ai/chart_analysis.py)
with guaranteed drift. All RugCharts surfaces import from here.

Pure functions over plain floats: no numpy/pandas dependency so this module
is trivially portable when rugcharts extracts into its own service.

Conventions:
- All series inputs are chronological (oldest first).
- Warm-up windows return None rather than partial/garbage values.
- Wilder smoothing for RSI (seed = simple average of first `period` changes,
  then exponential smoothing with alpha=1/period).
"""

from __future__ import annotations

from typing import Any


def sma(values: list[float], period: int) -> float | None:
    """Simple moving average of the last `period` values, or None if short."""
    if period <= 0 or len(values) < period:
        return None
    window = values[-period:]
    return sum(window) / period


def ema_series(values: list[float], period: int) -> list[float | None]:
    """EMA series with SMA seed. Indices < period-1 are None."""
    if period <= 0 or not values:
        return [None] * len(values)
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period  # SMA seed
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder's RSI. Returns None during warm-up; flat series -> 50.0."""
    if period <= 0 or len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0.0, diff))
        losses.append(max(0.0, -diff))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0 and avg_gain == 0:
        return 50.0  # perfectly flat — no direction information
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float | None]:
    """MACD legs at the latest bar: dif (fast-slow), dea (signal), hist."""
    if fast <= 0 or slow <= 0 or signal <= 0 or fast >= slow:
        return {"dif": None, "dea": None, "hist": None}
    ema_fast = ema_series(closes, fast)
    ema_slow = ema_series(closes, slow)
    dif_series: list[float | None] = [
        f - s if (f is not None and s is not None) else None for f, s in zip(ema_fast, ema_slow, strict=False)
    ]
    # Signal line: EMA over defined dif values only (skip warm-up Nones).
    defined = [d for d in dif_series if d is not None]
    dea_defined = ema_series(defined, signal)
    dif = dif_series[-1]
    dea = dea_defined[-1] if defined else None
    hist = (dif - dea) * 2 if (dif is not None and dea is not None) else None
    return {"dif": dif, "dea": dea, "hist": hist}


def bollinger(closes: list[float], period: int = 20, mult: float = 2.0) -> dict[str, float | None]:
    """Bollinger bands (population std) at the latest bar."""
    mid = sma(closes, period)
    if mid is None:
        return {"upper": None, "middle": None, "lower": None}
    window = closes[-period:]
    variance = sum((c - mid) ** 2 for c in window) / period
    std = variance**0.5
    return {"upper": mid + mult * std, "middle": mid, "lower": mid - mult * std}


def volume_snapshot(volumes: list[float], period: int = 20) -> dict[str, Any]:
    """Volume SMA + current + qualitative trend label."""
    vol_sma = sma(volumes, period)
    current = volumes[-1] if volumes else 0.0
    trend = "NORMAL"
    if vol_sma and vol_sma > 0:
        if current > vol_sma * 1.5:
            trend = "HIGH"
        elif current < vol_sma * 0.5:
            trend = "LOW"
    return {"volume_sma_20": vol_sma, "volume_current": current, "volume_trend": trend}


def compute_ta_snapshot(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Full TA snapshot from OHLCV candles (chronological).

    Canonical shape consumed by /rugcharts/intel, /rugcharts/ta and the
    frontend indicator panels. Returns {"available": False, ...} when there
    is insufficient data instead of raising.
    """
    closes = [float(c["close"]) for c in candles]
    volumes = [float(c.get("volume", 0) or 0) for c in candles]
    if len(candles) < 20:
        return {
            "available": False,
            "reason": "insufficient_ohlcv",
            "candle_count": len(candles),
        }

    sma7 = sma(closes, 7)
    sma20 = sma(closes, 20)
    rsi14 = rsi(closes, 14)
    bb = bollinger(closes, 20)
    macd_legs = macd(closes)
    vol = volume_snapshot(volumes, 20)

    last = closes[-1]
    return {
        "available": True,
        "candle_count": len(candles),
        "sma_7": round(sma7, 10) if sma7 is not None else None,
        "sma_20": round(sma20, 10) if sma20 is not None else None,
        "rsi_14": round(rsi14, 2) if rsi14 is not None else None,
        "bb_upper": round(bb["upper"], 10) if bb["upper"] is not None else None,
        "bb_lower": round(bb["lower"], 10) if bb["lower"] is not None else None,
        "bb_middle": round(bb["middle"], 10) if bb["middle"] is not None else None,
        "macd_dif": round(macd_legs["dif"], 10) if macd_legs["dif"] is not None else None,
        "macd_dea": round(macd_legs["dea"], 10) if macd_legs["dea"] is not None else None,
        "macd_hist": round(macd_legs["hist"], 10) if macd_legs["hist"] is not None else None,
        "price_vs_sma20": "ABOVE" if sma20 is not None and last > sma20 else "BELOW",
        "rsi_signal": ("OVERBOUGHT" if (rsi14 or 50) > 70 else "OVERSOLD" if (rsi14 or 50) < 30 else "NEUTRAL"),
        "trend": ("BULLISH" if (sma7 is not None and sma20 is not None and sma7 > sma20) else "BEARISH"),
        **vol,
    }
