"""RugCharts WIN-7 tests: shared TA module + wallet classifier.

Oracle policy: every expected value below is derived INDEPENDENTLY of the
implementation — hand-computed arithmetic or a differently-structured
derivation written out in the test. The implementation is never imported
into the expectation.

Golden values:
- sma(1..20, 20) = (1+20)/2 = 10.5
- population variance of 1..20 = (n^2-1)/12 = 399/12 = 33.25 → std = sqrt(33.25)
- Wilder RSI period=4 on [4,3,2,1,2]: diffs [-1,-1,-1,+1] → seed gain .25,
  loss .75 → RS = 1/3 → RSI = 100 - 100/(1+1/3) = 25.0 exactly
- EMA([1,2,3], p=3): SMA seed = 2.0 at index 2; indices 0,1 are None
"""

from __future__ import annotations

import math

import pytest

from app.routers.rugcharts.classifier import classify_trades, label_summary
from app.routers.rugcharts.ta import (
    bollinger,
    compute_ta_snapshot,
    ema_series,
    macd,
    rsi,
    sma,
    volume_snapshot,
)

# ── Shared TA module ─────────────────────────────────────────────────────


class TestSma:
    def test_sma_of_first_twenty_integers(self) -> None:
        assert sma(list(range(1, 21)), 20) == pytest.approx(10.5)

    def test_sma_window_is_last_n(self) -> None:
        # Last 3 of [1..10] = [8,9,10] → mean 9.0
        assert sma(list(range(1, 11)), 3) == pytest.approx(9.0)

    def test_sma_insufficient_data_returns_none(self) -> None:
        assert sma([1.0, 2.0], 5) is None


class TestRsi:
    def test_all_gains_is_hundred(self) -> None:
        assert rsi([float(i) for i in range(1, 16)], 14) == pytest.approx(100.0)

    def test_all_losses_is_zero(self) -> None:
        assert rsi([float(i) for i in range(15, 0, -1)], 14) == pytest.approx(0.0)

    def test_flat_series_is_fifty(self) -> None:
        assert rsi([5.0] * 15, 14) == pytest.approx(50.0)

    def test_hand_computed_wilder_case(self) -> None:
        # diffs: -1,-1,-1,+1 → avg_gain=.25 avg_loss=.75 → RS=1/3 → RSI=25.0
        closes = [4.0, 3.0, 2.0, 1.0, 2.0]
        assert rsi(closes, 4) == pytest.approx(25.0)

    def test_warmup_returns_none(self) -> None:
        assert rsi([1.0, 2.0], 14) is None


class TestEmaSeries:
    def test_sma_seed_value(self) -> None:
        out = ema_series([1.0, 2.0, 3.0], 3)
        assert out[0] is None
        assert out[1] is None
        assert out[2] == pytest.approx(2.0)  # (1+2+3)/3

    def test_recursive_step_independent_check(self) -> None:
        # k = 2/(p+1); after seed 2.0, next = 4*k + 2*(1-k) with p=3, k=.5 → 3.0
        out = ema_series([1.0, 2.0, 3.0, 4.0], 3)
        assert out[3] == pytest.approx(3.0)


class TestMacd:
    def test_strictly_rising_series_has_positive_dif(self) -> None:
        closes = [100.0 + i for i in range(40)]
        legs = macd(closes)
        assert legs["dif"] is not None
        assert legs["dif"] > 0  # type: ignore[union-attr]
        assert legs["dea"] is not None
        assert legs["hist"] is not None

    def test_short_series_returns_nones(self) -> None:
        legs = macd([1.0, 2.0, 3.0])
        assert legs["dif"] is None
        assert legs["dea"] is None
        assert legs["hist"] is None

    def test_hist_identity(self) -> None:
        # hist must equal (dif - dea) * 2 by definition.
        closes = [50.0 + math.sin(i / 3.0) * 5 + i * 0.1 for i in range(60)]
        legs = macd(closes)
        if legs["dif"] is not None and legs["dea"] is not None and legs["hist"] is not None:
            assert legs["hist"] == pytest.approx((legs["dif"] - legs["dea"]) * 2)


class TestBollinger:
    def test_golden_bands_on_first_twenty_integers(self) -> None:
        bands = bollinger([float(i) for i in range(1, 21)], 20)
        expected_std = math.sqrt(33.25)  # (n^2-1)/12 variance, n=20
        assert bands["middle"] == pytest.approx(10.5)
        assert bands["upper"] == pytest.approx(10.5 + 2 * expected_std)
        assert bands["lower"] == pytest.approx(10.5 - 2 * expected_std)

    def test_insufficient_data(self) -> None:
        assert bollinger([1.0], 20)["upper"] is None


class TestVolumeSnapshotAndTaSnapshot:
    def test_volume_trend_labels(self) -> None:
        base = [100.0] * 20
        assert volume_snapshot([*base, 200.0])["volume_trend"] == "HIGH"
        assert volume_snapshot([*base, 10.0])["volume_trend"] == "LOW"
        assert volume_snapshot([*base, 110.0])["volume_trend"] == "NORMAL"

    def _candles(self, n: int) -> list[dict]:
        return [{"time": i, "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 100.0} for i in range(n)]

    def test_snapshot_insufficient_candles(self) -> None:
        snap = compute_ta_snapshot(self._candles(10))
        assert snap["available"] is False
        assert snap["reason"] == "insufficient_ohlcv"

    def test_snapshot_full_shape(self) -> None:
        snap = compute_ta_snapshot(self._candles(30))
        assert snap["available"] is True
        for key in ("sma_7", "sma_20", "rsi_14", "bb_upper", "bb_lower", "trend", "volume_current"):
            assert key in snap


# ── Wallet-behavior classifier ───────────────────────────────────────────


def _trade(time: int, vol: float, side: str = "buy", wallet: str = "", tx: str = "") -> dict:
    return {
        "time": time,
        "price_usd": 0.01,
        "volume_usd": vol,
        "side": side,
        "tx_hash": tx or f"tx{time}{vol}{wallet}",
        "wallet": wallet,
        "chain": "solana",
    }


class TestClassifier:
    def test_whale_label_by_notional(self) -> None:
        out = classify_trades([_trade(1000, 15_000.0, wallet="w1")])
        assert out[0]["wallet_label"] == "whale"

    def test_machine_gun_regularity_flags_bot(self) -> None:
        # Six trades exactly 60s apart → gap CV = 0 < 0.35 → bot.
        trades = [_trade(1000 + i * 60, 50.0, wallet="botter") for i in range(6)]
        out = classify_trades(trades)
        assert all(t["wallet_label"] == "bot" for t in out)

    def test_round_number_sizing_flags_bot(self) -> None:
        # Irregular timing but every size exactly $500 → round-size bot.
        times = [1000, 1300, 1900, 2700, 3600]
        trades = [_trade(t, 500.0, wallet="sizer") for t in times]
        out = classify_trades(trades)
        assert {t["wallet_label"] for t in out} == {"bot"}

    def test_sniper_buy_then_quick_sell(self) -> None:
        trades = [
            _trade(1000, 900.0, "buy", wallet="sniper"),
            _trade(1060, 880.0, "sell", wallet="sniper"),
        ]
        out = classify_trades(trades)
        assert {t["wallet_label"] for t in out} == {"sniper"}

    def test_bundle_same_second_multi_wallet(self) -> None:
        trades = [
            _trade(1000, 300.0, wallet="a"),
            _trade(1000, 300.0, wallet="b"),
            _trade(1000, 300.0, wallet="c"),
        ]
        out = classify_trades(trades)
        assert {t["wallet_label"] for t in out} == {"bundle"}

    def test_organic_trader_stays_swap(self) -> None:
        trades = [
            _trade(1000, 123.45, wallet="human"),
            _trade(1873, 77.31, "sell", wallet="human"),
            _trade(3311, 210.9, wallet="human"),
        ]
        out = classify_trades(trades)
        assert {t["wallet_label"] for t in out} == {"swap"}

    def test_whale_beats_other_labels(self) -> None:
        trades = [_trade(1000 + i * 60, 20_000.0, wallet="whalebot") for i in range(6)]
        out = classify_trades(trades)
        assert {t["wallet_label"] for t in out} == {"whale"}

    def test_unattributed_trade_gets_swap_or_whale_only(self) -> None:
        out = classify_trades([_trade(1000, 55.0)])  # no wallet
        assert out[0]["wallet_label"] == "swap"

    def test_label_summary_counts(self) -> None:
        trades = [
            _trade(1000, 15_000.0, wallet="w"),
            _trade(1001, 10.0, wallet="x"),
        ]
        summary = label_summary(classify_trades(trades))
        assert summary.get("whale") == 1
        assert summary.get("swap") == 1

    def test_confidence_present_and_bounded(self) -> None:
        out = classify_trades([_trade(1000, 15_000.0, wallet="w")])
        assert 0 <= out[0]["label_confidence"] <= 100
