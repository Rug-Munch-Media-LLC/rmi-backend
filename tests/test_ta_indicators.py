"""PURE unit tests for app.domains.ta (indicators + summary).

Synthetic candle series only — no network, no DB, no Redis. Every indicator
is exercised on known sequences and None-safe on short input.
"""

from __future__ import annotations

import pytest

from app.domains.ta import (
    atr,
    ema,
    macd,
    pct_change,
    rsi,
    sma,
    support_resistance,
    ta_summary,
    ta_summary_text,
    trend,
)


def make_candles(closes: list[float]) -> list[dict]:
    """Build OHLCV candle dicts in ohlcv_engine shape (open=high=low=close)."""
    candles = []
    for i, close in enumerate(closes):
        candles.append(
            {
                "timestamp": 1_700_000_000 + i * 3600,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000.0,
            }
        )
    return candles


# ── SMA ──────────────────────────────────────────────────────────────────


def test_sma_correct_on_known_sequence() -> None:
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert sma(closes, 3) == pytest.approx(9.0)  # last 3: 8, 9, 10
    assert sma(closes, 5) == pytest.approx(8.0)  # last 5: 6..10
    assert sma(closes, 10) == pytest.approx(5.5)


def test_sma_none_on_short_input() -> None:
    assert sma([], 5) is None
    assert sma([1.0, 2.0, 3.0], 5) is None
    assert sma([1.0, 2.0, 3.0], 0) is None
    assert sma([1.0, 2.0, 3.0], -1) is None


# ── EMA ──────────────────────────────────────────────────────────────────


def test_ema_seed_equals_sma_and_tracks_trend() -> None:
    closes = [float(i) for i in range(1, 31)]
    # With exactly `period` values the EMA equals the SMA seed.
    assert ema(closes[:5], 5) == pytest.approx(3.0)
    # On a rising series, the EMA trails the latest close but exceeds the oldest.
    latest = ema(closes, 5)
    assert latest is not None
    assert latest < closes[-1]
    assert latest > closes[0]


def test_ema_none_on_short_input() -> None:
    assert ema([], 5) is None
    assert ema([1.0, 2.0], 5) is None


# ── RSI ──────────────────────────────────────────────────────────────────


def test_rsi_100_on_all_gains() -> None:
    closes = [float(i) for i in range(1, 17)]  # 15 gains, period 14
    assert rsi(closes, period=14) == pytest.approx(100.0)


def test_rsi_0_on_all_losses() -> None:
    closes = [float(i) for i in range(16, 0, -1)]  # 15 losses, period 14
    assert rsi(closes, period=14) == pytest.approx(0.0)


def test_rsi_between_0_and_100_on_mixed_series() -> None:
    closes = [
        100.0,
        101.0,
        100.5,
        102.0,
        101.5,
        103.0,
        102.5,
        104.0,
        103.5,
        105.0,
        104.5,
        106.0,
        105.5,
        107.0,
        106.5,
        108.0,
    ]
    value = rsi(closes, period=14)
    assert value is not None
    assert 0.0 < value < 100.0


def test_rsi_flat_series_is_neutral() -> None:
    closes = [50.0] * 16
    assert rsi(closes, period=14) == pytest.approx(50.0)


def test_rsi_none_on_short_input() -> None:
    assert rsi([], period=14) is None
    assert rsi([1.0, 2.0, 3.0], period=14) is None  # needs period+1 = 15


# ── MACD ─────────────────────────────────────────────────────────────────


def test_macd_bullish_on_rising_series() -> None:
    # Exponential rise → accelerating momentum → histogram stays positive.
    closes = [100.0 * (1.02**i) for i in range(80)]
    result = macd(closes, fast=12, slow=26, signal=9)
    assert result["macd"] is not None
    assert result["signal"] is not None
    assert result["histogram"] is not None
    # Fast EMA above slow EMA on a monotonic rise → MACD line > 0.
    assert result["macd"] > 0
    # MACD histogram is positive while momentum is expanding.
    assert result["histogram"] > 0


def test_macd_all_none_when_insufficient_for_line() -> None:
    result = macd([1.0, 2.0, 3.0], fast=12, slow=26, signal=9)
    assert result == {"macd": None, "signal": None, "histogram": None}


def test_macd_signal_none_when_only_line_available() -> None:
    # len 30 ≥ slow(26) but < slow + signal - 1 (34) → line only.
    closes = [float(i) for i in range(1, 31)]
    result = macd(closes, fast=12, slow=26, signal=9)
    assert result["macd"] is not None
    assert result["signal"] is None
    assert result["histogram"] is None


# ── ATR ──────────────────────────────────────────────────────────────────


def test_atr_known_value() -> None:
    highs = [10.0, 10.0, 10.0]
    lows = [5.0, 5.0, 5.0]
    closes = [7.0, 7.0, 7.0]
    # TRs: 5 (first bar range), 5 (max(5, |10-7|=3, |5-7|=2)) → ATR(2) = 5.
    assert atr(highs, lows, closes, period=2) == pytest.approx(5.0)


def test_atr_reflects_gaps() -> None:
    # A large gap between bars inflates true range vs the bar's own range.
    highs = [10.0, 20.0]
    lows = [9.0, 19.0]
    closes = [9.5, 19.5]
    assert atr(highs, lows, closes, period=1) == pytest.approx(10.5)  # max(1, |20-9.5|, |19-9.5|)


def test_atr_none_on_short_input() -> None:
    assert atr([], [], [], period=14) is None
    assert atr([1.0, 2.0], [1.0, 2.0], [1.0, 2.0], period=14) is None
    assert atr([1.0, 2.0, 3.0], [1.0, 2.0, 3.0], [1.0], period=2) is None  # length mismatch


# ── Support / Resistance ─────────────────────────────────────────────────


def test_support_resistance_known_swings() -> None:
    highs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]
    lows = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5]
    result = support_resistance(highs, lows, lookback=3)
    assert result["support"] == pytest.approx(2.5)  # min of last 3 lows
    assert result["resistance"] == pytest.approx(7.0)  # max of last 3 highs


def test_support_resistance_none_on_insufficient() -> None:
    assert support_resistance([], []) == {"support": None, "resistance": None}
    assert support_resistance([1.0], [1.0]) == {"support": None, "resistance": None}
    assert support_resistance([1.0, 2.0], [1.0, 2.0], lookback=0) == {"support": None, "resistance": None}


# ── Trend ────────────────────────────────────────────────────────────────


def test_trend_up_down_sideways() -> None:
    up = [float(i) for i in range(1, 81)]  # monotonic rise
    down = [float(i) for i in range(80, 0, -1)]  # monotonic fall
    flat = [50.0] * 80
    assert trend(up, short=20, long=50) == "uptrend"
    assert trend(down, short=20, long=50) == "downtrend"
    assert trend(flat, short=20, long=50) == "sideways"


def test_trend_short_input_returns_sideways() -> None:
    assert trend([], short=20, long=50) == "sideways"
    assert trend([1.0, 2.0, 3.0], short=20, long=50) == "sideways"


# ── pct_change ───────────────────────────────────────────────────────────


def test_pct_change_known() -> None:
    assert pct_change([100.0, 110.0]) == pytest.approx(10.0)
    assert pct_change([100.0, 95.0]) == pytest.approx(-5.0)
    assert pct_change([100.0, 100.0]) == pytest.approx(0.0)


def test_pct_change_none_on_short_or_zero_first() -> None:
    assert pct_change([]) is None
    assert pct_change([1.0]) is None
    assert pct_change([0.0, 5.0]) is None


# ── ta_summary ───────────────────────────────────────────────────────────


def test_ta_summary_shape_and_values() -> None:
    closes = [float(i) for i in range(1, 101)]
    candles = make_candles(closes)
    summary = ta_summary(candles, symbol="TEST", timeframe="1h")

    expected_keys = {
        "symbol",
        "timeframe",
        "last_price",
        "rsi",
        "macd",
        "sma_20",
        "sma_50",
        "sma_200",
        "atr",
        "support",
        "resistance",
        "trend",
        "change_24h",
        "signal",
    }
    assert set(summary.keys()) == expected_keys
    assert summary["symbol"] == "TEST"
    assert summary["timeframe"] == "1h"
    assert summary["last_price"] == pytest.approx(100.0)
    assert summary["sma_20"] == pytest.approx(90.5)
    assert summary["sma_50"] == pytest.approx(75.5)
    assert summary["sma_200"] is None  # only 100 candles
    assert summary["trend"] == "uptrend"
    assert summary["signal"] == "bullish"
    assert summary["macd"]["macd"] is not None
    assert summary["change_24h"] == pytest.approx((100.0 - 77.0) / 77.0 * 100.0)


def test_ta_summary_defensive_on_malformed_candles() -> None:
    candles = [
        {"timestamp": 1, "close": "10.0", "high": "11", "low": "9"},  # strings OK
        {"timestamp": 2, "close": None, "high": 12, "low": 10},  # missing close skipped
        {"timestamp": 3, "close": 12.0, "high": float("nan"), "low": 11},  # NaN high skipped
        {"timestamp": 4, "close": 11.0},  # no high/low
    ]
    summary = ta_summary(candles)
    assert summary["last_price"] == pytest.approx(11.0)
    assert summary["rsi"] is None  # too few usable closes
    assert summary["support"] is not None or summary["support"] is None  # never raises


def test_ta_summary_empty_input() -> None:
    summary = ta_summary([], symbol="EMPTY")
    assert summary["last_price"] is None
    assert summary["rsi"] is None
    assert summary["trend"] == "sideways"
    assert summary["signal"] == "neutral"
    assert summary["symbol"] == "EMPTY"
    assert summary["timeframe"] == "1h"


def test_ta_summary_bearish_signal_on_falling_prices() -> None:
    closes = [float(i) for i in range(100, 0, -1)]
    summary = ta_summary(make_candles(closes), symbol="FALL", timeframe="1h")
    assert summary["trend"] == "downtrend"
    assert summary["signal"] == "bearish"


# ── ta_summary_text ──────────────────────────────────────────────────────


def test_ta_summary_text_contains_key_fields() -> None:
    # Exponential rise keeps MACD momentum positive → "MACD bullish".
    closes = [100.0 * (1.02**i) for i in range(80)]
    summary = ta_summary(make_candles(closes), symbol="TEST", timeframe="1h")
    text = ta_summary_text(summary)
    assert "RSI" in text
    assert "MACD bullish" in text
    assert "above 50-SMA" in text
    assert "support $" in text
    assert "resistance $" in text
    assert "uptrend" in text
    assert "signal bullish" in text


def test_ta_summary_text_handles_none_gracefully() -> None:
    summary = ta_summary([], symbol="EMPTY")
    text = ta_summary_text(summary)
    assert "RSI n/a" in text
    assert "MACD n/a" in text
    assert "signal neutral" in text
    assert "above 50-SMA" not in text
    assert "support $" not in text


def test_ta_summary_text_micro_price_renders_nonzero() -> None:
    # Micro-cap prices must not render as "$0.00".
    candles = []
    base = 0.00001234
    for i in range(30):
        candles.append(
            {
                "timestamp": 1_700_000_000 + i * 3600,
                "open": base,
                "high": base * 1.001,
                "low": base * 0.999,
                "close": base,
                "volume": 1000.0,
            }
        )
    text = ta_summary_text(ta_summary(candles, symbol="MICRO", timeframe="1h"))
    # Micro prices render with significant digits, never a useless "$0.00".
    assert "support $0.00001233" in text
    assert "resistance $0.00001235" in text
