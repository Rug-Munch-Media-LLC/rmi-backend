"""Unit tests for rugmunchbot /ta technical analysis.

Covers the pure indicator math (ta_indicators.py) and the HTML card
renderer (format_ta_card in handlers/scanning.py). Network calls and
Telegram updates are not exercised here.
"""

from __future__ import annotations

import pytest

from app.domains.telegram.rugmunchbot.handlers.scanning import format_ta_card
from app.domains.telegram.rugmunchbot.ta_indicators import (
    analyze_candles,
    ema,
    macd,
    rsi,
    sma,
)


def _candle(i: int, close: float, volume: float = 1000.0) -> dict:
    return {
        "time": i,
        "open": close * 0.99,
        "high": close * 1.02,
        "low": close * 0.98,
        "close": close,
        "volume": volume,
    }


def _uptrend(n: int = 60, start: float = 1.0, factor: float = 1.01) -> list[dict]:
    candles = []
    price = start
    for i in range(n):
        price *= factor
        candles.append(_candle(i, price, volume=1000 + i * 100))
    return candles


def _downtrend(n: int = 60, start: float = 1.0, factor: float = 0.99) -> list[dict]:
    candles = []
    price = start
    for i in range(n):
        price *= factor
        candles.append(_candle(i, price, volume=1000))
    return candles


class TestSMA:
    def test_alignment_and_warmup(self) -> None:
        out = sma([1, 2, 3, 4], 2)
        assert out[:1] == [None]
        assert out[1:] == [1.5, 2.5, 3.5]

    def test_short_series(self) -> None:
        assert sma([1, 2], 5) == [None, None]


class TestEMA:
    def test_monotonic_period2_is_linear(self) -> None:
        out = ema([1, 2, 3, 4, 5], 2)
        assert out[0] is None
        assert out[1:] == pytest.approx([1.5, 2.5, 3.5, 4.5])

    def test_warmup_none_before_seed(self) -> None:
        out = ema([1, 2, 3], 3)
        assert out[:2] == [None, None]
        assert out[2] == pytest.approx(2.0)

    def test_insufficient_data(self) -> None:
        assert ema([1, 2], 5) == [None, None]


class TestRSI:
    def test_all_gains_is_100(self) -> None:
        closes = [float(i) for i in range(1, 20)]
        assert rsi(closes, 14) == 100.0

    def test_all_losses_is_0(self) -> None:
        closes = [float(100 - i) for i in range(20)]
        assert rsi(closes, 14) == 0.0

    def test_flat_series_is_50(self) -> None:
        assert rsi([1.0] * 20, 14) == 50.0

    def test_known_value_wilder(self) -> None:
        # Verified by hand: 15 deltas, Wilder seed + 1 smoothing step.
        closes = [
            44,
            44.34,
            44.09,
            44.15,
            43.61,
            44.33,
            44.83,
            45.10,
            45.42,
            45.84,
            46.08,
            45.89,
            46.03,
            45.61,
            46.28,
            46.28,
        ]
        assert rsi(closes, 14) == pytest.approx(72.44, abs=0.01)

    def test_insufficient_data_is_none(self) -> None:
        assert rsi([1.0, 2.0, 3.0], 14) is None


class TestMACD:
    def test_structure(self) -> None:
        m = macd([float(i) for i in range(40)])
        assert set(m) == {"line", "signal", "histogram"}
        assert len(m["line"]) == 40
        assert len(m["signal"]) == 40
        assert len(m["histogram"]) == 40

    def test_uptrend_histogram_positive(self) -> None:
        # Quadratic acceleration → rising momentum → MACD above signal.
        m = macd([1.0 + 0.01 * i + 0.001 * i * i for i in range(60)])
        assert m["histogram"][-1] is not None
        assert m["histogram"][-1] > 0

    def test_accelerating_decline_histogram_negative(self) -> None:
        # Quadratic acceleration down → falling momentum → MACD below signal.
        m = macd([100.0 - 0.5 * i - 0.03 * i * i for i in range(60)])
        assert any(h is not None and h < 0 for h in m["histogram"][30:])

    def test_flat_series_histogram_near_zero(self) -> None:
        m = macd([1.0] * 40)
        assert m["histogram"][-1] == pytest.approx(0.0, abs=1e-9)


class TestAnalyzeCandles:
    def test_empty_returns_none(self) -> None:
        assert analyze_candles([]) is None

    def test_malformed_returns_none(self) -> None:
        assert analyze_candles([{"time": 1}]) is None
        assert analyze_candles("not-a-list") is None  # type: ignore[arg-type]

    def test_uptrend_bullish(self) -> None:
        a = analyze_candles(_uptrend())
        assert a is not None
        assert a["trend"] == "BULLISH"
        assert a["rsi"] == 100.0
        assert a["ema_cross"] == "BULLISH CROSS"
        assert a["macd_label"] == "Bullish"
        assert a["support"] < a["resistance"]

    def test_downtrend_bearish(self) -> None:
        a = analyze_candles(_downtrend())
        assert a is not None
        assert a["trend"] == "BEARISH"
        assert a["rsi"] == 0.0
        assert a["ema_cross"] == "BEARISH CROSS"

    def test_flat_neutral(self) -> None:
        a = analyze_candles([_candle(i, 1.0) for i in range(60)])
        assert a is not None
        assert a["trend"] == "NEUTRAL"
        assert a["rsi"] == 50.0

    def test_volume_rising_flag(self) -> None:
        candles = []
        price = 1.0
        for i in range(30):
            price *= 1.01
            vol = 10_000 if i >= 28 else 1_000
            candles.append(_candle(i, price, volume=vol))
        a = analyze_candles(candles)
        assert a is not None
        assert a["volume_trend"] == "Rising"
        assert a["volume_ratio"] is not None and a["volume_ratio"] > 1.5

    def test_volume_declining_flag(self) -> None:
        candles = []
        price = 1.0
        for i in range(30):
            price *= 1.01
            vol = 1_000 if i >= 28 else 10_000
            candles.append(_candle(i, price, volume=vol))
        a = analyze_candles(candles)
        assert a is not None
        assert a["volume_trend"] == "Declining"
        assert a["volume_ratio"] < 0.5

    def test_rsi_label_bands(self) -> None:
        over = analyze_candles(_uptrend())
        assert over is not None and over["rsi_label"] == "Overbought"
        under = analyze_candles(_downtrend())
        assert under is not None and under["rsi_label"] == "Oversold"
        flat = analyze_candles([_candle(i, 1.0) for i in range(60)])
        assert flat is not None and flat["rsi_label"] == "Neutral"

    def test_support_resistance_from_recent_window(self) -> None:
        candles = _uptrend(40)
        a = analyze_candles(candles)
        assert a is not None
        # Resistance is the max high of the last 20 candles.
        window = candles[-20:]
        assert a["resistance"] == pytest.approx(max(c["high"] for c in window))
        assert a["support"] == pytest.approx(min(c["low"] for c in window))


class TestFormatTACard:
    def _analysis(self, **overrides: object) -> dict:
        base: dict = {
            "candles": 60,
            "price": 1.2345,
            "change_pct": 12.3,
            "high": 1.5,
            "low": 0.9,
            "trend": "BULLISH",
            "ema9": 1.2,
            "ema21": 1.1,
            "ema_cross": "BULLISH CROSS",
            "rsi": 62.3,
            "rsi_label": "Neutral",
            "macd_line": 0.0001234,
            "macd_signal": 0.0001,
            "macd_hist": 0.0000234,
            "macd_label": "Bullish",
            "volume_ratio": 2.1,
            "volume_trend": "Rising",
            "support": 0.95,
            "resistance": 1.45,
        }
        base.update(overrides)
        return base

    def _meta(self, **overrides: object) -> dict:
        meta: dict = {
            "name": "TestCoin",
            "symbol": "TST",
            "address": "0xabc123",
            "chain": "ethereum",
            "price": 1.2345,
            "change_24h": 5.2,
            "rug_score": 45,
            "rug_level": "CAUTION",
        }
        meta.update(overrides)
        return meta

    def test_card_contains_core_sections(self) -> None:
        text = format_ta_card(self._analysis(), self._meta())
        assert "Technical Analysis" in text
        assert "<b>TestCoin</b> (TST)" in text
        assert "<code>0xabc123</code>" in text
        assert "Trend: BULLISH" in text
        assert "RSI-14: <b>62.3</b>" in text
        assert "EMA-9:" in text and "EMA-21:" in text
        assert "MACD:" in text
        assert "Volume:" in text
        assert "Key Levels" in text
        assert "Resistance:" in text
        assert "Support:" in text
        assert "Rug Risk" in text
        assert "CAUTION" in text
        assert "45/100" in text
        assert "not financial advice" in text

    def test_card_handles_missing_indicators(self) -> None:
        a = self._analysis(
            ema9=None,
            ema21=None,
            ema_cross="NEUTRAL",
            rsi=None,
            rsi_label="N/A",
            macd_line=None,
            macd_label="N/A",
            volume_ratio=None,
            volume_trend="Steady",
            support=None,
            resistance=None,
        )
        text = format_ta_card(a, self._meta())
        assert "RSI-14: N/A" in text
        assert "Volume:" in text
        assert "Resistance:" not in text
        assert "Support:" not in text

    def test_card_omits_rug_overlay_when_absent(self) -> None:
        meta = self._meta(rug_score=None, rug_level=None)
        text = format_ta_card(self._analysis(), meta)
        assert "Rug Risk" not in text

    def test_card_is_compact(self) -> None:
        text = format_ta_card(self._analysis(), self._meta())
        assert len(text) < 1100
        assert text.count("\n") < 30


class TestTrendArrow:
    def test_arrows(self) -> None:
        from app.domains.telegram.rugmunchbot.handlers.scanning import _ta_arrow

        assert _ta_arrow("BULLISH") == "\U0001f7e2"
        assert _ta_arrow("BEARISH") == "\U0001f534"
        assert _ta_arrow("NEUTRAL") == "\u26aa"
        assert _ta_arrow("UNKNOWN") == "\u26aa"
