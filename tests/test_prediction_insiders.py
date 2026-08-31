"""Tests for prediction-market insider detection (app.domains.prediction.insider_detection)."""

from __future__ import annotations

from app.domains.prediction.insider_detection import (
    MIN_POSITIONS,
    MIN_WIN_RATE,
    aggregate_by_wallet,
    analyze_wallet,
    binomial_p_value,
)


def _market(cid: str, outcome: str | None, resolution_time: float | None = 1_000_000) -> dict:
    return {"id": cid, "question": f"Q {cid}", "outcome": outcome, "resolution_time": resolution_time, "volume": 1e6}


def _trade(wallet: str, cid: str, side: str, amount: float, price: float, ts: float) -> dict:
    return {
        "id": f"t-{wallet}-{cid}-{side}",
        "market_id": cid,
        "wallet": wallet,
        "side": side,
        "amount": amount,
        "price": price,
        "timestamp": ts,
        "outcome": "",
        "username": "",
    }


class TestBinomialPValue:
    def test_perfect_record_is_significant(self):
        assert binomial_p_value(10, 10) < 0.001

    def test_coin_flip_is_not_significant(self):
        assert binomial_p_value(5, 10) > 0.5

    def test_zero_total_returns_one(self):
        assert binomial_p_value(0, 0) == 1.0

    def test_bounds(self):
        assert 0.0 <= binomial_p_value(3, 10) <= 1.0


class TestAnalyzeWallet:
    def _setup(self, wins: int, losses: int, wallet: str = "0xabc"):
        trades = []
        markets = {}
        for i in range(wins):
            cid = f"w{i}"
            markets[cid] = _market(cid, "Yes")
            trades.append(_trade(wallet, cid, "buy", 100.0, 0.5, 999_000))
        for i in range(losses):
            cid = f"l{i}"
            markets[cid] = _market(cid, "No")
            trades.append(_trade(wallet, cid, "buy", 100.0, 0.5, 999_000))
        return wallet, trades, markets

    def test_smart_money_label(self):
        wallet, trades, markets = self._setup(8, 2)  # 80% win rate
        result = analyze_wallet({"address": wallet}, trades, markets)
        assert result is not None
        assert result["smart_money"] is True
        assert result["win_rate"] == 0.8
        assert result["total_positions"] == 10
        assert result["wins"] == 8
        assert result["profit"] > 0

    def test_below_thresholds_not_smart_money(self):
        wallet, trades, markets = self._setup(4, 4)  # 50% win rate
        result = analyze_wallet({"address": wallet}, trades, markets)
        assert result is not None
        assert result["smart_money"] is False

    def test_too_few_positions_returns_none(self):
        wallet, trades, markets = self._setup(3, 1)  # 4 < MIN_POSITIONS
        assert analyze_wallet({"address": wallet}, trades, markets) is None

    def test_unresolved_markets_ignored(self):
        wallet, trades, markets = self._setup(6, 2)
        markets["w0"]["outcome"] = None  # one market unresolved
        result = analyze_wallet({"address": wallet}, trades, markets)
        assert result is not None
        assert result["total_positions"] == 7

    def test_suspicion_score_bands(self):
        # 10/10 wins at 0.5 price → p-value tiny → critical-level suspicion.
        wallet, trades, markets = self._setup(10, 0)
        result = analyze_wallet({"address": wallet}, trades, markets)
        assert result is not None
        assert result["suspicion_level"] in ("high", "critical")
        assert result["p_value"] < 0.001


class TestAggregateByWallet:
    def test_groups_and_usernames(self):
        trades = [
            _trade("0xa", "m1", "buy", 10, 0.5, 1),
            _trade("0xa", "m2", "sell", 50, 0.5, 2),
            _trade("0xb", "m1", "buy", 10, 0.5, 3),
        ]
        trades[0]["username"] = "alice"
        out = aggregate_by_wallet(trades)
        by_addr = {w["address"]: w for w in out}
        assert len(by_addr["0xa"]["trades"]) == 2
        assert by_addr["0xa"]["usernames"] == ["alice"]
        assert len(by_addr["0xb"]["trades"]) == 1

    def test_skips_walletless_trades(self):
        assert aggregate_by_wallet([_trade("", "m1", "buy", 1, 1, 1)]) == []


class TestThresholds:
    def test_mission_thresholds(self):
        assert MIN_WIN_RATE == 0.60
        assert MIN_POSITIONS == 5
