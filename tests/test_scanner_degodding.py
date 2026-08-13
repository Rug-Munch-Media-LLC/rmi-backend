"""Pure tests for the scanner-domain degodding split (2026-08-14).

Verifies that helper groups extracted from the god modules are importable
from their new sibling submodules AND re-exported from the original module
with the SAME object identity — so every existing importer and test keeps
working with zero behavior change. No network, no DB.
"""

from __future__ import annotations

import app.domains.scanner._rules as rrs_rules
import app.domains.scanner._signals as rrs_signals
import app.domains.scanner._text as rrs_text
import app.domains.scanner._weights as rrs_weights
import app.domains.scanner.rug_risk_score as rrs
import app.domains.scanners._config as lv_config
import app.domains.scanners._models as lv_models
import app.domains.scanners.liquidity_verifier as lv

# ── rug_risk_score: constants ──────────────────────────────────────────


def test_weights_submodule_importable() -> None:
    assert rrs_weights.HONEYPOT_WEIGHT == 40
    assert rrs_weights.CRITICAL_MIN == 70
    assert rrs_weights.WEIGHTS["kol_verified"] == -rrs_weights.KOL_MITIGATION
    # backward-compat aliases
    assert rrs_weights.BUNDLED_WEIGHT is rrs_weights.BUNDLE_WEIGHT
    assert rrs_weights.DEPLOYER_REPEAT_WEIGHT is rrs_weights.REPEAT_DEPLOYER_WEIGHT


def test_weights_reexported_from_original_same_object() -> None:
    for name in ("HONEYPOT_WEIGHT", "CRITICAL_MIN", "KOL_MITIGATION", "WEIGHTS", "SINGLE_POOL_MAX"):
        assert getattr(rrs, name) is getattr(rrs_weights, name), name


# ── rug_risk_score: signal predicates ──────────────────────────────────


def test_signals_submodule_importable() -> None:
    assert rrs_signals._truthy_scalar(1) is True
    assert rrs_signals._truthy_scalar(0) is False
    assert rrs_signals._to_float("85") == 85.0
    assert rrs_signals._to_pct("85%") == 85.0
    assert rrs_signals._probe({"a": {"b": 1}}, (("a", "b"),)) == 1
    assert rrs_signals._very_young(500.0) is True
    assert rrs_signals._very_young(7200.0) is False
    assert rrs_signals._lp_unlocked(False) is True
    assert rrs_signals._lp_unlocked(90.0) is False  # 90% locked > 50% max


def test_signals_reexported_from_original_same_object() -> None:
    for name in ("_truthy_scalar", "_signal_bool", "_to_float", "_to_pct", "_probe", "_kol_verified"):
        assert getattr(rrs, name) is getattr(rrs_signals, name), name


# ── rug_risk_score: rule table + renderers ─────────────────────────────


def test_rules_submodule_importable() -> None:
    assert len(rrs_rules._RULES) == 28
    assert rrs_rules._RULES[0][0] == "honeypot"
    assert rrs_rules._RULES[-1][0] == "single_pool"
    groups = {rule[3] for rule in rrs_rules._RULES}
    assert groups == {"CONTRACT", "SUPPLY", "LIQUIDITY", "TRADING", "DEPLOYER", "SOCIAL", "MOMENTUM"}


def test_rules_reexported_from_original_same_object() -> None:
    assert rrs._RULES is rrs_rules._RULES


def test_text_submodule_importable() -> None:
    assert rrs_text._verdict_for(90) == "critical"
    assert rrs_text._verdict_for(50) == "high"
    assert rrs_text._verdict_for(30) == "medium"
    assert rrs_text._verdict_for(5) == "low"
    assert rrs_text._GROUP_OF["honeypot"] == "CONTRACT"
    assert rrs_text._GROUP_OF["kol_verified"] == "MITIGATION"
    out = rrs_text.risk_verdict_text({"score": 75, "verdict": "critical", "flags": ["honeypot"]})
    assert "CRITICAL" in out
    assert "honeypot" in out


def test_text_reexported_from_original_same_object() -> None:
    for name in ("_verdict_for", "risk_verdict_text", "risk_analysis_text"):
        assert getattr(rrs, name) is getattr(rrs_text, name), name


# ── rug_risk_score: full public API surface intact ─────────────────────


def test_original_all_exports_resolve() -> None:
    for name in rrs.__all__:
        assert hasattr(rrs, name), f"missing from module namespace: {name}"
    assert rrs.rug_risk_score({"honeypot": True})["score"] >= rrs.CRITICAL_MIN
    assert rrs.rug_risk_score({})["score"] == 0
    # fail-open path
    assert rrs.rug_risk_score(None)["score"] == 0


# ── liquidity_verifier: models + config ────────────────────────────────


def test_lv_models_submodule_importable() -> None:
    assert lv_models.LockStatus.BURNED.value == "burned"
    assert len(lv_models.SOLANA_BURN_ADDRESSES) == 2
    detail = lv_models.LockDetail(locker_address="0x0", platform="UNCX")
    assert detail.is_legit is True
    assert detail.percentage_locked == 0.0


def test_lv_models_reexported_from_original_same_object() -> None:
    for name in ("LockStatus", "LockDetail", "LockReport", "SOLANA_BURN_ADDRESSES"):
        assert getattr(lv, name) is getattr(lv_models, name), name


def test_lv_config_submodule_importable() -> None:
    assert "ethereum" in lv_config.ETHERSCAN_NETWORKS
    assert lv_config.HELIUS_RPC_URL.startswith("http")
    assert lv_config.MORALIS_DATA_URL.startswith("https")


def test_lv_config_reexported_from_original_same_object() -> None:
    for name in ("ETHERSCAN_NETWORKS", "MORALIS_DATA_URL", "HELIUS_RPC_URL", "HELIUS_API_KEY"):
        assert getattr(lv, name) is getattr(lv_config, name), name
