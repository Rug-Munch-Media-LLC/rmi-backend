"""Unit tests for the Polymarket opportunity primitives (Gamma + Data API v2)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.domains.prediction.opportunities.gamma import normalize_market
from app.domains.prediction.opportunities.holders import build_evidence
from app.domains.prediction.opportunities.scoring import WEIGHTS, score_market


def _row(condition_id: str = "0xcond", **overrides) -> dict:
    row = {
        "conditionId": condition_id,
        "slug": "will-x",
        "question": "Will X happen?",
        "outcomes": json.dumps(["Yes", "No"]),
        "outcomePrices": json.dumps(["0.42", "0.58"]),
        "volumeNum": 50_000,
        "liquidityNum": 9_000,
        "endDate": (datetime.now(UTC) + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "oneDayPriceChange": 0.12,
    }
    row.update(overrides)
    return row


def test_weights_are_documented_and_sum_to_one():
    assert set(WEIGHTS) == {
        "volume",
        "liquidity",
        "odds_movement",
        "near_resolution",
        "catalyst",
        "whale_concentration",
    }
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9


def test_normalize_market_parses_stringified_arrays():
    market = normalize_market(_row())
    assert market is not None
    assert market["outcomes"] == ["Yes", "No"]
    assert market["prices"] == [0.42, 0.58]
    assert market["yes_price"] == 0.42
    assert market["odds_delta"] == 0.12
    assert market["odds_delta_window"] == "1d"
    assert market["hours_to_resolution"] > 0
    assert market["market_url"] == "https://polymarket.com/market/will-x"


def test_normalize_market_prefers_one_hour_change_when_present():
    market = normalize_market(_row(oneHourPriceChange=0.03))
    assert market is not None
    assert market["odds_delta"] == 0.03
    assert market["odds_delta_window"] == "1h"


def test_normalize_market_rejects_mismatched_outcomes():
    assert normalize_market(_row(outcomePrices=json.dumps(["0.5"]))) is None


def test_normalize_market_rejects_missing_question():
    assert normalize_market(_row(question="")) is None


def test_normalize_market_marks_missing_odds_delta_with_reason():
    market = normalize_market(_row(oneDayPriceChange=None))
    assert market is not None
    assert market["odds_delta"] is None
    assert market["odds_delta_reason"] == "gamma_returned_no_price_change_field"


def test_build_evidence_computes_top5_share():
    payload = {
        "holders": [
            {"proxyWallet": "0xaaa", "amount": 700.0, "name": "whale-a", "outcome": "Yes"},
            {"proxyWallet": "0xbbb", "amount": 200.0, "outcome": "No"},
            {"proxyWallet": "0xccc", "amount": 100.0, "outcome": "Yes"},
        ]
    }
    evidence = build_evidence("0xcond", payload, "https://data-api.polymarket.com/holders?market=0xcond")
    assert evidence.available is True
    assert evidence.holder_count == 3
    assert evidence.top5_share == pytest.approx(1.0)
    assert evidence.top_holders[0].wallet == "0xaaa"
    assert evidence.top_holders[0].share == pytest.approx(0.7)


def test_build_evidence_reports_reason_on_unknown_shape():
    evidence = build_evidence("0xcond", {"unexpected": True}, "https://data-api.polymarket.com/holders")
    assert evidence.available is False
    assert evidence.reason == "data_api_holders_unparsable"
    assert evidence.concentration_score is None


def test_score_market_reports_null_components_with_reason():
    market = normalize_market(
        {
            "conditionId": "0xn",
            "slug": "n",
            "question": "Will nothing happen?",
            "outcomes": json.dumps(["Yes", "No"]),
            "outcomePrices": json.dumps(["0.5", "0.5"]),
            "volumeNum": 0,
            "liquidityNum": 0,
        }
    )
    assert market is not None
    score, components = score_market(market, None)
    by_name = {c.name: c for c in components}
    assert 0.0 <= score <= 1.0
    assert by_name["odds_movement"].raw is None
    assert by_name["odds_movement"].contribution == 0.0
    assert by_name["odds_movement"].reason == "gamma_returned_no_price_change_field"
    assert by_name["whale_concentration"].reason == "holder_probe_not_selected"
    assert by_name["near_resolution"].reason == "end_date_unparsable"


def test_catalyst_keyword_raises_score():
    plain = normalize_market(_row(condition_id="0xplain", question="Will nothing happen?"))
    hot = normalize_market(_row(condition_id="0xhot", question="Will the SEC approve it?"))
    assert plain is not None
    assert hot is not None
    assert score_market(hot, None)[0] > score_market(plain, None)[0]
