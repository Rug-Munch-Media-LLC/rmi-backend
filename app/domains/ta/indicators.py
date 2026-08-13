"""Pure technical-analysis indicator math.

Dependency-free: only stdlib ``math``/``statistics``. Every public function
accepts plain ``list[float]`` price series (closes, and highs/lows where
needed) and returns ``None`` (or ``None``-containing dicts / a safe string)
on insufficient or short input — never raises.

Conventions:
    - Series are ordered oldest → newest (index -1 is the latest value).
    - Period arguments must be positive ints; invalid periods yield ``None``.
    - ``ema``/``rsi``/``atr`` use Wilder-style smoothing (alpha = 2/(period+1)),
      seeded with the SMA of the first ``period`` values.
"""

from __future__ import annotations

from statistics import fmean

__all__ = [
    "atr",
    "ema",
    "macd",
    "pct_change",
    "rsi",
    "sma",
    "support_resistance",
    "trend",
]

# Relative slope threshold (fraction of the short SMA) below which a series is
# considered flat. 0.5% avoids calling tiny wiggles a trend.
TREND_SLOPE_EPS = 0.005


def _is_valid_period(period: int) -> bool:
    return isinstance(period, int) and period > 0


def sma(closes: list[float], period: int) -> float | None:
    """Simple moving average of the last ``period`` closes.

    Returns ``None`` when fewer than ``period`` closes are available.
    """
    if not _is_valid_period(period) or len(closes) < period:
        return None
    return fmean(closes[-period:])


def _ema_series(closes: list[float], period: int) -> list[float | None]:
    """Full EMA series (oldest → newest), ``None``-padded before warm-up."""
    n = len(closes)
    if not _is_valid_period(period) or n < period:
        return [None] * n
    alpha = 2.0 / (period + 1)
    out: list[float | None] = [None] * (period - 1)
    ema_value = fmean(closes[:period])
    out.append(ema_value)
    for price in closes[period:]:
        ema_value = (price - ema_value) * alpha + ema_value
        out.append(ema_value)
    return out


def ema(closes: list[float], period: int) -> float | None:
    """Exponential moving average (Wilder alpha = 2/(period+1)).

    Seeded with the SMA of the first ``period`` closes. Returns ``None`` when
    fewer than ``period`` closes are available.
    """
    series = _ema_series(closes, period)
    return series[-1] if series else None


def rsi(closes: list[float], period: int = 14) -> float | None:
    """Relative Strength Index with Wilder's smoothing.

    Requires at least ``period + 1`` closes (one price change per period bar).
    Returns 0..100; when average loss is zero the RSI is 100 (rising series)
    or 50 (perfectly flat series — RS is undefined, so neutral is reported).
    """
    n = len(closes)
    if not _is_valid_period(period) or n < period + 1:
        return None

    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        avg_gain += max(change, 0.0)
        avg_loss += max(-change, 0.0)
    avg_gain /= period
    avg_loss /= period

    for i in range(period + 1, n):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period

    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, float | None]:
    """MACD line, signal line and histogram (12/26/9 by default).

    - ``macd`` = EMA(fast) - EMA(slow). Non-``None`` once ``len >= slow``.
    - ``signal`` = EMA(signal) of the MACD line; needs ``slow + signal - 1``
      closes (``signal`` MACD samples to seed).
    - ``histogram`` = macd - signal.

    Returns ``{"macd": None, "signal": None, "histogram": None}`` when there is
    not enough data to compute the MACD line at all.
    """
    empty: dict[str, float | None] = {"macd": None, "signal": None, "histogram": None}
    if not _is_valid_period(fast) or not _is_valid_period(slow) or not _is_valid_period(signal):
        return empty
    if slow <= fast:
        return empty
    if len(closes) < slow:
        return empty

    ema_fast = _ema_series(closes, fast)
    ema_slow = _ema_series(closes, slow)
    macd_values: list[float] = []
    for f_val, s_val in zip(ema_fast, ema_slow, strict=True):
        if f_val is not None and s_val is not None:
            macd_values.append(f_val - s_val)

    result: dict[str, float | None] = {
        "macd": macd_values[-1] if macd_values else None,
        "signal": None,
        "histogram": None,
    }
    if len(macd_values) >= signal:
        signal_value = _ema_series(macd_values, signal)[-1]
        result["signal"] = signal_value
        if signal_value is not None and result["macd"] is not None:
            result["histogram"] = result["macd"] - signal_value
    return result


def atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float | None:
    """Average True Range (Wilder's smoothing).

    True Range for the first bar is ``high - low``; subsequent bars use
    ``max(high-low, |high-prev_close|, |low-prev_close|)``. Requires at least
    ``period + 1`` bars.
    """
    n = len(closes)
    if not _is_valid_period(period) or n < period + 1:
        return None
    if len(highs) != n or len(lows) != n:
        return None

    true_ranges: list[float] = [highs[0] - lows[0]]
    for i in range(1, n):
        true_ranges.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )

    atr_value = fmean(true_ranges[1 : period + 1])
    for i in range(period + 1, n):
        atr_value = (atr_value * (period - 1) + true_ranges[i]) / period
    return atr_value


def support_resistance(
    highs: list[float],
    lows: list[float],
    lookback: int = 20,
) -> dict[str, float | None]:
    """Recent swing support/resistance from the last ``lookback`` candles.

    - ``support`` = lowest low in the window (recent swing low)
    - ``resistance`` = highest high in the window (recent swing high)

    Requires at least 2 candles and a positive ``lookback``; a shorter window
    still uses whatever is available (never raises). Returns ``None`` levels
    when there is no usable data.
    """
    none_levels: dict[str, float | None] = {"support": None, "resistance": None}
    if not _is_valid_period(lookback):
        return none_levels
    if len(highs) < 2 or len(lows) < 2:
        return none_levels
    window = min(lookback, len(lows))
    return {
        "support": min(lows[-window:]),
        "resistance": max(highs[-window:]),
    }


def trend(closes: list[float], short: int = 20, long: int = 50) -> str:
    """Trend classification via short/long SMA cross + short-SMA slope.

    Returns ``"uptrend"`` when the short SMA sits above the long SMA and is
    still rising (relative slope > 0.5%), ``"downtrend"`` for the mirror image,
    and ``"sideways"`` otherwise — including when the series is too short, in
    which case the safe string is returned instead of ``None`` (matches the
    "never raise" contract; callers key on the three literals).
    """
    if not _is_valid_period(short) or not _is_valid_period(long) or long <= short:
        return "sideways"
    n = len(closes)
    if n < long + 1:
        return "sideways"

    sma_short = sma(closes, short)
    sma_long = sma(closes, long)
    prev_short = sma(closes[:-1], short)
    if sma_short is None or sma_long is None or prev_short is None or prev_short == 0:
        return "sideways"

    slope = (sma_short - prev_short) / abs(prev_short)
    if sma_short > sma_long and slope > TREND_SLOPE_EPS:
        return "uptrend"
    if sma_short < sma_long and slope < -TREND_SLOPE_EPS:
        return "downtrend"
    return "sideways"


def pct_change(closes: list[float]) -> float | None:
    """Percent change from the first to the latest close.

    Returns ``None`` with fewer than 2 closes or a zero first close.
    """
    if len(closes) < 2:
        return None
    first = closes[0]
    if first == 0:
        return None
    return (closes[-1] - first) / first * 100.0
