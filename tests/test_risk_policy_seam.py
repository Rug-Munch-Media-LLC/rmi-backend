"""Contract tests for the public half of the risk-policy seam.

These tests must pass in a bare clone of this repository — no private
package, no credentials, no network. They pin:

1. the interface (``RiskPolicy``) and the engine (``RulePolicy``);
2. the example policy is illustrative and clearly labelled as such;
3. the compat shim exposes every name the public API promised;
4. the calibration itself is NOT in this repository.
"""

from __future__ import annotations

import pytest

from app.domains.scanner import _weights
from app.domains.scanner.risk_policy import (
    PolicyUnavailable,
    RiskPolicy,
    RulePolicy,
    example_policy,
    load_policy,
    probe,
    reset_policy,
)
from app.domains.scanner.rug_risk_score import (
    risk_analysis_text,
    risk_verdict_text,
    rug_risk_score,
)


def _spec() -> dict:
    return {
        "name": "test",
        "rules": (
            ("alpha", 40, "alpha fired", "CONTRACT", (((("a",),), lambda v: v is True),)),
            ("beta", 30, "beta fired", "LIQUIDITY", (((("b",), ("nested", "b")), lambda v: v is True),)),
        ),
        "hard_floor_flags": ("alpha",),
        "bands": {"critical": 70, "high": 45, "medium": 20},
        "constants": {"CRITICAL_MIN": 70},
        "groups": {"alpha": "CONTRACT", "beta": "LIQUIDITY"},
    }


# ── interface ─────────────────────────────────────────────────────────


def test_example_policy_satisfies_the_protocol() -> None:
    assert isinstance(example_policy(), RiskPolicy)


def test_engine_scores_sums_of_weights() -> None:
    policy = RulePolicy.from_spec(_spec())
    assert policy.evaluate({"b": True})["score"] == 30
    assert policy.evaluate({"a": True, "b": True})["score"] == 70
    assert policy.evaluate({"a": True, "b": True})["flags"] == ["alpha", "beta"]


def test_engine_is_monotonic_and_clamped() -> None:
    policy = RulePolicy.from_spec(_spec())
    low = policy.evaluate({"b": True})["score"]
    high = policy.evaluate({"a": True, "b": True})["score"]
    assert low <= high
    assert 0 <= high <= 100


def test_engine_hard_floor_applies_after_clamp() -> None:
    policy = RulePolicy.from_spec(_spec())
    result = policy.evaluate({"a": True})
    assert result["score"] >= 70
    assert result["verdict"] == "critical"


def test_engine_fails_open() -> None:
    policy = RulePolicy.from_spec(_spec())
    for garbage in (None, [], "x", 3):  # type: ignore[arg-type]
        result = policy.evaluate(garbage)  # type: ignore[arg-type]
        assert result["score"] == 0
        assert result["flags"] == []


def test_engine_nested_key_paths() -> None:
    policy = RulePolicy.from_spec(_spec())
    assert policy.evaluate({"nested": {"b": True}})["score"] == 30


def test_bad_spec_is_rejected() -> None:
    with pytest.raises(PolicyUnavailable):
        RulePolicy.from_spec({"name": "x"})
    with pytest.raises(PolicyUnavailable):
        RulePolicy.from_spec({**_spec(), "bands": {"critical": 70}})


def test_probe_returns_first_present_value() -> None:
    assert probe({"b": 1}, (("a",), ("b",))) == 1
    assert probe({"a": None, "b": 2}, (("a",), ("b",))) == 2
    assert probe({}, (("a",),)) is None


# ── resolution ────────────────────────────────────────────────────────


def test_load_policy_is_cached_and_resettable() -> None:
    first = load_policy()
    assert load_policy() is first
    reset_policy()
    assert load_policy() is not first


def test_named_provider_that_cannot_be_imported_is_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RMI_RISK_POLICY", "does.not.exist:default_policy")
    reset_policy()
    with pytest.raises(PolicyUnavailable):
        load_policy()
    reset_policy()


def test_env_provider_is_used_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RMI_RISK_POLICY", "tests.risk_policy_fixture:default_policy")
    reset_policy()
    assert load_policy().name == "fixture-policy"
    reset_policy()


# ── public surface + shim ─────────────────────────────────────────────


def test_scorer_public_api_still_resolves() -> None:
    import app.domains.scanner.rug_risk_score as rrs

    for name in rrs.__all__:
        assert hasattr(rrs, name), f"public API name disappeared: {name}"


def test_scorer_returns_the_documented_shape() -> None:
    result = rug_risk_score({"honeypot": True})
    assert set(result) == {"score", "verdict", "flags", "reasons", "analysis"}
    assert isinstance(result["score"], int)
    assert 0 <= result["score"] <= 100
    assert result["verdict"] in {"low", "medium", "high", "critical"}
    assert risk_verdict_text(result).startswith("🔴")
    assert "Risk Score:" in risk_analysis_text(result)


def test_weights_shim_exposes_names_not_method() -> None:
    assert isinstance(_weights.WEIGHTS, dict)
    # aliases are the same object, by construction
    assert _weights.BUNDLED_WEIGHT is _weights.BUNDLE_WEIGHT
    assert _weights.DEPLOYER_REPEAT_WEIGHT is _weights.REPEAT_DEPLOYER_WEIGHT
    # WEIGHTS is derived from the rules actually applied, so it cannot drift
    assert set(_weights.WEIGHTS) == {rule[0] for rule in load_policy().rules}


# ── the point: the recipe is not here ─────────────────────────────────


def test_calibration_modules_are_gone_from_this_repository() -> None:
    import importlib

    for module in ("app.domains.scanner._rules", "app.domains.scanner._signals"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)


def test_example_policy_is_not_a_reduced_copy_of_the_method() -> None:
    """The example must be obviously illustrative, not a usable calibration."""
    example = example_policy()
    assert example.name.startswith("example")
    assert len(example.rules) <= 3
