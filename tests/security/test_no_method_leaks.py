"""Leak regressions: public/paid payloads expose scores + bands, never rubrics.

If a weight, an intercept, a per-factor numeric contribution or a source list
comes back into any of these responses, these tests fail — that is the point.
"""

from __future__ import annotations

import inspect
from typing import Any

from app.domains.gmgn_intel.scoring import public_breakdown
from app.routers import death_clock, gmgn_intel as gmgn_router
from app.routers.rugcharts.helpers.bias import predictive_bias
from app.routers.x402_advanced_tools._envelope import EnvelopePayload, envelope, tier_for

FEATURES: dict[str, Any] = {
    "symbol": "TEST",
    "liquidity_usd": 500,
    "holder_count": 12,
    "lp_locked_pct": 0,
    "owner_renounced": False,
    "mint_authority": True,
    "honeypot_risk": "honeypot",
    "buy_tax": 0.2,
    "sell_tax": 0.3,
    "age_days": 0,
    "volume_to_liquidity": 9,
}


def test_death_clock_payload_has_no_numeric_factors() -> None:
    payload = death_clock._payload("0xabc", "base", FEATURES)
    assert payload["reasons"]
    assert all(isinstance(r, str) for r in payload["reasons"])
    assert all(isinstance(v, str) for v in payload["factors"].values()), "factors must be directions, not contributions"
    assert set(payload["factors"].values()) <= {"shortens", "extends", "neutral"}
    assert "log-days" not in payload["explanation"]
    assert not any(isinstance(v, int | float) and not isinstance(v, bool) for v in payload["factors"].values())
    assert "weights" not in payload
    assert "intercept" not in payload


def test_death_clock_rejects_unverified_x402_parameter() -> None:
    params = inspect.signature(death_clock.predict_death_clock).parameters
    assert "x_x402_sig" not in params, "an unverified payment header must not be accepted"


def test_gmgn_breakdown_is_banded() -> None:
    scored = {
        "score": 12.5,
        "grade": "C",
        "blocked": False,
        "penalties": [],
        "weights_version": "internal",
        "components": [{"name": "security", "value": 1.0, "weight": 0.5, "points": 10.0}],
        "missing": [],
    }
    out = public_breakdown(scored)
    assert out["components"] == [{"name": "security", "band": "positive"}]
    assert "weights_version" not in out
    assert "weights" not in out
    for component in out["components"]:
        assert set(component) == {"name", "band"}


def test_descriptor_exposes_no_weight_table() -> None:
    from app.domains.gmgn_intel.service import GMGNIntelService

    desc = GMGNIntelService().descriptor()
    assert "weights" not in desc, "removed, not null"
    assert "weights_version" not in desc
    assert desc["scoring"]["model"] == "multi-signal band"

    route = next(r for r in gmgn_router.router.routes if getattr(r, "path", "") == "/descriptor")
    assert route.dependant.dependencies, "descriptor must require auth"


def test_rug_probability_envelope_has_no_rubric() -> None:
    signals = [{"signal": "honeypot_detected", "band": "extreme", "detail": "cannot sell"}]
    tier, recommendation = tier_for(40)
    payload = envelope(EnvelopePayload("0xabc", "base", 40, tier, recommendation, signals, 0.0))
    assert payload["reasons"] == ["cannot sell"]
    assert "sources_used" not in payload, "removed, not null"
    assert "_confidence" not in payload
    for item in payload["signals"]:
        assert "weight" not in item
        assert isinstance(item["band"], str)


def test_intel_bias_has_no_numeric_impacts() -> None:
    breakdown = {"bot_volume_pct": 55.0, "buy_pressure": 0.7, "confidence": "high"}
    bias = predictive_bias(breakdown)
    assert bias["method"] == "banded_rule_fusion"
    assert bias["reasons"]
    assert all(isinstance(r, str) for r in bias["reasons"])
    for factor in bias["factors"]:
        assert "impact" not in factor, "numeric impact removed, not null"
        assert isinstance(factor["band"], str)
    assert -100 <= bias["score"] <= 100
