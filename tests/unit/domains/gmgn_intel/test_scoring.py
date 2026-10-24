"""Our score: component math, blocking, weights, ordering."""

from __future__ import annotations

from app.domains.gmgn_intel.scoring import WEIGHTS, score_token


def _token(**extra: object) -> dict[str, object]:
    base = {
        "symbol": "TST",
        "volume_24h_usd": 250_000,
        "liquidity_usd": 60_000,
        "holders": 900,
        "buys_24h": 100,
        "sells_24h": 60,
        "social": {"twitter": "https://x.com/t", "website": None, "telegram": None},
        "security": {"rug_ratio": 0.1, "top_10_holder_rate": 0.2},
    }
    base.update(extra)
    return base


def test_weights_are_normalized() -> None:
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_score_is_explainable_and_bounded() -> None:
    result = score_token(_token())
    assert 0 <= result["score"] <= 100
    assert [c["name"] for c in result["components"]] == list(WEIGHTS)
    assert abs(sum(c["points"] for c in result["components"]) - result["score"]) < 0.01
    assert result["blocked"] is False
    assert result["missing"] == []


def test_stronger_token_scores_higher() -> None:
    weak = score_token(_token(volume_24h_usd=2_000, liquidity_usd=1_500, holders=15, buys_24h=1, sells_24h=9))
    strong = score_token(_token(volume_24h_usd=2_000_000, liquidity_usd=400_000, holders=5_000))
    assert strong["score"] > weak["score"]
    assert strong["grade"] in {"A", "B"}


def test_honeypot_blocks_and_zeroes_the_score() -> None:
    result = score_token(_token(security={"is_honeypot": True}))
    assert result["blocked"] is True
    assert result["score"] == 0.0
    assert "honeypot_or_unsellable" in result["penalties"]


def test_risk_penalties_reduce_security_component() -> None:
    clean = score_token(_token())
    dirty = score_token(_token(security={"rug_ratio": 0.9, "top_10_holder_rate": 0.8, "is_wash_trading": True}))
    assert dirty["score"] < clean["score"]
    assert {"high_rug_ratio", "top10_concentration", "wash_trading"} <= set(dirty["penalties"])


def test_missing_inputs_are_named_not_zeroed_silently() -> None:
    result = score_token({"symbol": "BARE"})
    assert set(result["missing"]) == set(WEIGHTS)
    assert result["score"] == 0.0
    assert result["grade"] == "F"
