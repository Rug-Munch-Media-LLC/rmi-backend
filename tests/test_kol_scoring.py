"""Dry tests for KOL track-record scoring (app.databus.kol_scoring)."""

from __future__ import annotations

from app.databus import kol_scoring as ks


def _setup() -> None:
    ks.clear_registry()
    # Good KOL: 3 calls, all winners, +30% avg return, no flags.
    for token, p0, p1 in [("GOOD1", 1.0, 1.35), ("GOOD2", 2.0, 2.6), ("GOOD3", 0.5, 0.62)]:
        ks.record_call_outcome("goodtrader", token, price_at_call=p0, price_after_24h=p1)
    ks.register_wallet_confidence("goodtrader", 0.9)
    # Bad KOL: calls a rugged token, has shill alerts, is a known scammer.
    ks.record_call_outcome("pumpdumper", "RUGCOIN", price_at_call=1.0, price_after_24h=0.05)
    ks.record_call_outcome("pumpdumper", "SHILLCOIN", price_at_call=1.0, price_after_24h=0.5)
    ks.register_rug_token("RUGCOIN")
    ks.register_shill_alert("pumpdumper", 4)
    ks.register_known_scammer("pumpdumper")
    # Mediocre KOL: 50/50 win rate, no rug, no flags.
    ks.record_call_outcome("midtrader", "MID1", price_at_call=1.0, price_after_24h=1.4, win=True)
    ks.record_call_outcome("midtrader", "MID2", price_at_call=1.0, price_after_24h=0.7, win=False)


def test_good_kol_scores_high() -> None:
    _setup()
    res = ks.score_kol("goodtrader")
    assert 60 <= res["score"] <= 100, res
    assert res["risk_level"] == "LOW"
    assert res["win_rate"] == 1.0
    assert res["wallet_confidence"] == 0.9


def test_scammer_is_floored() -> None:
    _setup()
    res = ks.score_kol("pumpdumper")
    assert 0 <= res["score"] <= 25, res
    assert res["risk_level"] == "HIGH"
    assert res["rug_rate"] == 0.5
    assert res["known_scammer"] is True
    assert res["shill_alert_count"] >= 4


def test_mid_kol_is_neutral_medium() -> None:
    _setup()
    res = ks.score_kol("midtrader")
    assert 30 <= res["score"] <= 70, res
    assert res["win_rate"] == 0.5
    assert res["risk_level"] in {"MEDIUM", "LOW"}


def test_unknown_kol_defaults_neutral() -> None:
    _setup()
    res = ks.score_kol("nobody_tracked")
    assert res["score"] == 50.0
    assert res["risk_level"] == "MEDIUM"


def test_get_and_leaderboard() -> None:
    _setup()
    ks.score_kol("goodtrader")
    ks.score_kol("pumpdumper")
    ks.score_kol("midtrader")
    got = ks.get_kol_score("goodtrader")
    assert got["found"] is True
    lb = ks.get_kol_leaderboard()
    assert lb["total_tracked"] == 3
    assert lb["leaderboard"][0]["handle"] == "goodtrader"
    assert lb["leaderboard"][-1]["handle"] == "pumpdumper"
