"""Pure tests for the 25-point rug-risk scorer (no network/DB).

Every test feeds a signals dict straight into ``rug_risk_score`` — the
scorer reads nothing but its input, so no fixtures, mocks, or I/O are
involved. Sibling enrichment modules are never imported.
"""

from __future__ import annotations

import pytest

from app.domains.scanner.rug_risk_score import (
    BUNDLE_WEIGHT,
    BUNDLED_WEIGHT,
    CRITICAL_MIN,
    DEPLOYER_REPEAT_WEIGHT,
    KOL_MITIGATION,
    REPEAT_DEPLOYER_WEIGHT,
    WASH_TRADING_WEIGHT,
    WEIGHTS,
    _bad_funder,
    _bundle_suspect,
    _coordinated_shill,
    _fresh_wallet,
    _kol_pump,
    _kol_verified,
    _low_liquidity,
    _lp_burned_pct,
    _lp_removed,
    _lp_unlocked,
    _probe,
    _renounced,
    _repeat_deployer,
    _rug_history,
    _signal_bool,
    _single_pool,
    _smart_money_exit,
    _sniper_cluster,
    _supply_concentrated,
    _to_pct,
    _truthy_scalar,
    _very_young,
    _volume_concentrated,
    _whale_exit,
    risk_analysis_text,
    risk_verdict_text,
    rug_risk_score,
)

# Every risk signal plus one input shape that fires ONLY that signal in
# isolation (flat + nested module-output forms exercised across the table).
ALL_SIGNALS: list[tuple[str, dict]] = [
    # CONTRACT
    ("honeypot", {"honeypot": True}),
    ("mintable", {"mintable": True}),
    ("freezable", {"freeze_authority": True}),
    ("lp_unlocked", {"lp_locked": False}),
    ("renounced", {"renounced": False}),
    ("proxy_upgradable", {"proxy_upgradable": True}),
    ("ownership_transferable", {"can_take_back_ownership": True}),
    # SUPPLY
    ("mint_supply_risk", {"mint_supply_risk": True}),
    ("supply_concentrated", {"supply_concentrated": True}),
    ("burn_disabled", {"burn_disabled": True}),
    # LIQUIDITY
    ("low_liquidity", {"liquidity": 5000}),
    ("lp_removed", {"lp_removed": True}),
    ("lp_burned_pct", {"lp_burned_pct": 20}),
    ("single_pool", {"pool_count": 1}),
    # TRADING
    ("wash_trading", {"wash_trading": True}),
    ("volume_concentrated", {"volume_concentrated": True}),
    ("bundle_suspect", {"bundled": True}),
    ("sniper_cluster", {"sniper_count": 5}),
    # DEPLOYER
    ("repeat_deployer", {"deployer_repeat": True}),
    ("rug_history", {"rug_history": True}),
    ("bad_funder", {"bad_funder": True}),
    ("fresh_wallet", {"fresh_wallet": True}),
    # SOCIAL
    ("known_scammer", {"known_scammer": True}),
    ("kol_pump", {"kol_pump": True}),
    ("coordinated_shill", {"coordinated_shill": True}),
    # MOMENTUM
    ("very_young", {"age_seconds": 300}),
    ("whale_exit", {"whale_flow": "exit"}),
    ("smart_money_exit", {"smart_money_sold": True}),
]


@pytest.mark.parametrize(("signal", "payload"), ALL_SIGNALS, ids=[s for s, _ in ALL_SIGNALS])
def test_each_signal_contributes_and_appears_in_analysis(signal, payload):
    result = rug_risk_score(payload)
    weight = WEIGHTS[signal]
    assert result["flags"] == [signal], f"{signal} should be the only flag fired"
    assert result["score"] >= weight, f"{signal} must contribute at least its weight"
    analysis_signals = [entry["signal"] for entry in result["analysis"]]
    assert signal in analysis_signals
    entry = next(e for e in result["analysis"] if e["signal"] == signal)
    assert entry["weight"] == weight
    assert entry["detail"]


def test_nested_module_output_is_key_tolerant():
    """Enrichment modules land as nested dicts — the scorer parses them."""
    result = rug_risk_score(
        {
            "security": {"honeypot": True, "bundle_suspect": True},
            "deployer": {"repeat_deployer": True, "rug_history": True, "prior_deployments": 6},
            "authority": {"freeze_authority": True, "lp_locked": 20, "renounced": False},
            "holders": {"top_holder_pct": 80, "sniper_count": 5},
            "gmgn": {"smart_money": {"net_bought": -12.0}},
            "shill": [{"pattern": "dump_after_pump", "severity": "high"}],
        }
    )
    for signal in (
        "honeypot",
        "bundle_suspect",
        "repeat_deployer",
        "rug_history",
        "freezable",
        "lp_unlocked",
        "renounced",
        "supply_concentrated",
        "sniper_cluster",
        "smart_money_exit",
        "kol_pump",
    ):
        assert signal in result["flags"], f"{signal} missing from nested input"
    assert result["score"] >= CRITICAL_MIN  # honeypot floor


def test_honeypot_mintable_known_scammer_is_critical_with_all_in_analysis():
    result = rug_risk_score({"honeypot": True, "mintable": True, "known_scammer": True})
    assert result["verdict"] == "critical"
    assert result["score"] == 100  # 40 + 35 + 40 capped
    for signal in ("honeypot", "mintable", "known_scammer"):
        assert signal in result["flags"]
        assert signal in [entry["signal"] for entry in result["analysis"]]
        assert len(result["reasons"]) == 3


def test_lone_honeypot_forces_critical():
    result = rug_risk_score({"honeypot": {"is_honeypot": True}})
    assert result["verdict"] == "critical"
    assert result["score"] >= CRITICAL_MIN
    assert result["analysis"][0]["signal"] == "honeypot"


def test_known_scammer_dict_is_critical():
    result = rug_risk_score({"known_scammer": {"known_scammer": True, "scammer_handle": "@x"}})
    assert result["verdict"] == "critical"
    assert result["score"] >= CRITICAL_MIN
    assert "deployer is a known scammer" in " ".join(result["reasons"])


def test_bundle_wash_repeat_deployer_is_high():
    result = rug_risk_score({"bundled": True, "wash_trading": {"suspected": True}, "deployer_repeat": True})
    assert result["verdict"] == "high"
    assert result["score"] == BUNDLE_WEIGHT + WASH_TRADING_WEIGHT + REPEAT_DEPLOYER_WEIGHT == 67
    assert {"bundle_suspect", "wash_trading", "repeat_deployer"} <= set(result["flags"])
    analysis_signals = [entry["signal"] for entry in result["analysis"]]
    for signal in ("bundle_suspect", "wash_trading", "repeat_deployer"):
        assert signal in analysis_signals


def test_whale_exit_young_low_lp_is_medium():
    result = rug_risk_score({"whale_flow": "exit", "age_seconds": 300, "liquidity": 1200})
    assert result["verdict"] == "medium"
    assert result["score"] == 15 + 12 + 12
    assert set(result["flags"]) == {"whale_exit", "very_young", "low_liquidity"}


def test_clean_signals_are_low_with_empty_analysis():
    assert rug_risk_score({}) == {"score": 0, "verdict": "low", "flags": [], "reasons": [], "analysis": []}
    # Absence of evidence is not a penalty: locked LP / missing fields stay clean.
    assert rug_risk_score({"lp_locked": True})["flags"] == []
    assert rug_risk_score({"lp_locked": None, "liquidity": None})["flags"] == []
    assert rug_risk_score({"renounced": None})["flags"] == []  # unknown != risky
    assert rug_risk_score({"lp_burned": True})["flags"] == []  # burned LP is safe


def test_score_monotonic_with_more_flags():
    base = rug_risk_score({"bundled": True, "wash_trading": True})
    extra = rug_risk_score({"bundled": True, "wash_trading": True, "honeypot": True})
    assert extra["score"] > base["score"]
    single = rug_risk_score({"bundled": True})
    assert single["score"] > 0
    assert single["verdict"] != "low"
    assert base["score"] > single["score"]
    # Adding a second risk flag to a single flag never lowers the score.
    pair = rug_risk_score({"bundled": True, "wash_trading": True})
    assert pair["score"] >= single["score"]


def test_lp_unlocked_variants():
    assert "lp_unlocked" in rug_risk_score({"lp_locked": False})["flags"]
    assert "lp_unlocked" in rug_risk_score({"lp_locked": 20})["flags"]
    assert "lp_unlocked" not in rug_risk_score({"lp_locked": 85})["flags"]
    assert "lp_unlocked" not in rug_risk_score({"lp_locked": "95%"})["flags"]
    assert "lp_unlocked" in rug_risk_score({"lp_unlocked": True})["flags"]


def test_inverted_protective_signals():
    # renounced / lp_burned fire on the RISKY state, not the safe one.
    assert "renounced" in rug_risk_score({"renounced": False})["flags"]
    assert "renounced" not in rug_risk_score({"renounced": True})["flags"]
    assert "lp_burned_pct" in rug_risk_score({"lp_burned_pct": 10})["flags"]
    assert "lp_burned_pct" not in rug_risk_score({"lp_burned_pct": 95})["flags"]


def test_kol_mitigation_reduces_score():
    risky = rug_risk_score({"mintable": True})
    mitigated = rug_risk_score({"mintable": True, "kol": {"kol_handle": "@alice"}})
    assert mitigated["score"] == risky["score"] - KOL_MITIGATION
    assert "kol_verified" in mitigated["flags"]
    assert mitigated["analysis"][-1]["signal"] == "kol_verified"
    assert mitigated["analysis"][-1]["weight"] == -KOL_MITIGATION


def test_verdict_text_renders_top_flags():
    result = rug_risk_score({"honeypot": True, "mintable": True})
    text = risk_verdict_text(result)
    assert "🔴 CRITICAL (75/100)" in text
    assert "honeypot · mintable" in text
    text2 = risk_verdict_text(rug_risk_score({"known_scammer": True, "mintable": True}))
    assert "deployer is known scammer" in text2


def test_verdict_text_clean():
    assert "🟢 LOW (0/100) — no significant risk flags" in risk_verdict_text(rug_risk_score({}))


def test_analysis_text_renders_grouped_breakdown():
    result = rug_risk_score({"honeypot": True, "mintable": True, "known_scammer": True})
    text = risk_analysis_text(result)
    lines = text.splitlines()
    assert lines[0] == "Risk Score: 100/100 — CRITICAL"
    assert any(line.startswith("• CONTRACT: honeypot (+40)") for line in lines)
    assert any(line.startswith("• CONTRACT: mintable (+35)") for line in lines)
    assert any(line.startswith("• SOCIAL: deployer is known scammer (+40)") for line in lines)
    # Groups order CONTRACT before SOCIAL.
    contract_line = next(line for line in lines if line.startswith("• CONTRACT: honeypot"))
    social_line = next(line for line in lines if line.startswith("• SOCIAL:"))
    assert lines.index(contract_line) < lines.index(social_line)


def test_analysis_text_renders_mitigation_and_clean():
    result = rug_risk_score({"mintable": True, "kol": {"kol_handle": "@alice"}})
    text = risk_analysis_text(result)
    assert "• MITIGATION: KOL verified (-10)" in text
    assert risk_analysis_text(rug_risk_score({})).endswith("no significant risk flags")
    assert risk_analysis_text(None) == "Risk Score: 0/100 — LOW\nno significant risk flags"


def test_analysis_items_carry_signal_weight_detail_shape():
    result = rug_risk_score({"honeypot": True, "mintable": True})
    assert all(set(entry) == {"signal", "weight", "detail"} for entry in result["analysis"])
    assert all(isinstance(entry["weight"], int) for entry in result["analysis"])


def test_defensive_inputs_never_raise():
    assert rug_risk_score(None)["verdict"] == "low"
    assert rug_risk_score([])["verdict"] == "low"
    assert rug_risk_score("garbage")["analysis"] == []
    result = rug_risk_score({"honeypot": "unknown", "bundled": [], "age_seconds": "nope"})
    assert result["score"] == 0
    # Positive whale inflow is NOT an exit signal.
    assert "whale_exit" not in rug_risk_score({"whale_flow": {"net": 500}})["flags"]
    assert "smart_money_exit" not in rug_risk_score({"smart_money": {"net_bought": 5.0}})["flags"]
    assert risk_verdict_text(None).startswith("🟢 LOW")


def test_backward_compat_aliases():
    assert BUNDLED_WEIGHT == BUNDLE_WEIGHT
    assert DEPLOYER_REPEAT_WEIGHT == REPEAT_DEPLOYER_WEIGHT


# ═══════════════════════════════════════════════════════════════════════
# Direct helper coverage — edge/error branches not hit via the public API
# ═══════════════════════════════════════════════════════════════════════


def test_truthy_scalar_numeric_and_unknown():
    assert _truthy_scalar(0) is False
    assert _truthy_scalar(3.14) is True
    assert _truthy_scalar([]) is False
    assert _truthy_scalar({"x": 1}) is True


def test_signal_bool_dict_key_and_scalar():
    assert _signal_bool({"mintable": "1"}, ("mintable",)) is True
    assert _signal_bool({"other": True}, ("mintable",)) is False
    assert _signal_bool(None, ("mintable",)) is False
    assert _signal_bool("true", ("mintable",)) is True


def test_to_pct_string_forms():
    assert _to_pct("85%") == 85.0
    assert _to_pct(" 42 ") == 42.0
    assert _to_pct("nope") is None
    assert _to_pct(33) == 33.0


def test_probe_exception_path():
    class _Bad:
        def __getitem__(self, key):
            raise RuntimeError("boom")

    assert _probe({"a": _Bad()}, (("a", "b"),)) is None
    assert _probe(None, (("a",),)) is None  # type: ignore[arg-type]
    assert _probe({"a": {"b": 1}}, (("a", "b"),)) == 1


def test_whale_exit_variants():
    assert _whale_exit(-10.0) is True
    assert _whale_exit(10.0) is False
    assert _whale_exit("exit") is True
    assert _whale_exit("outflow") is True
    assert _whale_exit("dump") is True
    assert _whale_exit("sell") is True
    assert _whale_exit("buy") is False
    assert _whale_exit({"net": -5}) is True
    assert _whale_exit({"flow": "exit"}) is True
    assert _whale_exit({"trend": True}) is True
    assert _whale_exit({"net": 5}) is False
    assert _whale_exit(None) is False


def test_lp_unlocked_dict_and_scalar_forms():
    assert _lp_unlocked({"unlocked": True}) is True
    assert _lp_unlocked({"unlocked": False}) is False
    assert _lp_unlocked({"lp_unlocked": True}) is True
    # Non-numeric "locked" value is inverted as a truthy flag.
    assert _lp_unlocked({"locked": "no"}) is True
    assert _lp_unlocked({"locked": "yes"}) is False
    # Numeric/bool locked falls through to the pct branch (bool is int subclass).
    assert _lp_unlocked({"locked": False}) is False
    assert _lp_unlocked({"pct": 30}) is True
    assert _lp_unlocked({"pct": 90}) is False
    assert _lp_unlocked(False) is True
    assert _lp_unlocked(True) is False
    assert _lp_unlocked(85) is False
    assert _lp_unlocked("85%") is False
    assert _lp_unlocked(None) is False
    assert _lp_unlocked({}) is False


def test_very_young_and_low_liquidity_dict_forms():
    assert _very_young({"age_seconds": 300}) is True
    assert _very_young({"launch_age_seconds": 7200}) is False
    assert _very_young({"age": "nope"}) is False
    assert _very_young({}) is False
    assert _low_liquidity({"liquidity_usd": 100}) is True
    assert _low_liquidity({"pooled_liquidity": 50_000}) is False
    assert _low_liquidity({"usd": "nope"}) is False
    assert _low_liquidity({}) is False


def test_renounced_variants():
    assert _renounced(0) is True
    assert _renounced(1) is False
    assert _renounced("no") is True
    assert _renounced("false") is True
    assert _renounced("not_renounced") is True
    assert _renounced("yes") is False
    assert _renounced({"renounced": False}) is True
    assert _renounced({"owner_renounced": True}) is False
    assert _renounced({}) is False
    assert _renounced(None) is False


def test_supply_concentrated_variants():
    assert _supply_concentrated(75) is True
    assert _supply_concentrated(40) is False
    assert _supply_concentrated("75%") is True
    assert _supply_concentrated("nope") is False
    assert _supply_concentrated({"supply_concentrated": True}) is True
    assert _supply_concentrated({"top_holder_pct": 80}) is True
    assert _supply_concentrated({"top10_holder_pct": 30}) is False
    assert _supply_concentrated({}) is False
    assert _supply_concentrated(True) is True


def test_lp_burned_pct_dict_forms():
    assert _lp_burned_pct({"lp_burned": True}) is False
    assert _lp_burned_pct({"lp_burned": False}) is True
    assert _lp_burned_pct({"lp_burned_pct": 30}) is True
    assert _lp_burned_pct({"burned_pct": 90}) is False
    assert _lp_burned_pct({}) is False
    assert _lp_burned_pct(True) is False
    assert _lp_burned_pct(30) is True


def test_single_pool_variants():
    assert _single_pool(True) is True
    assert _single_pool(1) is True
    assert _single_pool(3) is False
    assert _single_pool({"single_pool": True}) is True
    assert _single_pool({"pool_count": 1}) is True
    assert _single_pool({"pair_count": 5}) is False
    assert _single_pool({"pairs": ["a"]}) is True
    assert _single_pool({"pairs": ["a", "b"]}) is False
    assert _single_pool({}) is False
    assert _single_pool("yes") is True


def test_volume_concentrated_variants():
    assert _volume_concentrated(55) is True
    assert _volume_concentrated(10) is False
    assert _volume_concentrated("45%") is True
    assert _volume_concentrated("nope") is False
    assert _volume_concentrated({"volume_concentrated": True}) is True
    assert _volume_concentrated({"top_maker_share_pct": 60}) is True
    assert _volume_concentrated({"share": 30}) is False
    assert _volume_concentrated({}) is False


def test_sniper_cluster_variants():
    assert _sniper_cluster(True) is True
    assert _sniper_cluster(3) is True
    assert _sniper_cluster(1) is False
    assert _sniper_cluster({"sniper_cluster": True}) is True
    assert _sniper_cluster({"sniper_count": 5}) is True
    assert _sniper_cluster({"snipers": 1}) is False
    assert _sniper_cluster({}) is False
    assert _sniper_cluster("sniper alert") is True
    assert _sniper_cluster("cluster detected") is True


def test_repeat_deployer_variants():
    assert _repeat_deployer(2) is True
    assert _repeat_deployer(1) is False
    assert _repeat_deployer({"repeat_deployer": True}) is True
    assert _repeat_deployer({"deployer_repeat": False}) is False
    assert _repeat_deployer({"prior_deployments": 6}) is True
    assert _repeat_deployer({"prior_deployments": 1}) is False
    assert _repeat_deployer({}) is False
    assert _repeat_deployer(True) is True


def test_fresh_wallet_variants():
    assert _fresh_wallet(100) is True
    assert _fresh_wallet(10_000_000) is False
    assert _fresh_wallet({"fresh_wallet": True}) is True
    assert _fresh_wallet({"first_deployment": True}) is True
    assert _fresh_wallet({"wallet_age_seconds": 60}) is True
    assert _fresh_wallet({"wallet_age_seconds": 999_999}) is False
    assert _fresh_wallet({}) is False
    assert _fresh_wallet("fresh") is True
    assert _fresh_wallet("new") is True
    assert _fresh_wallet("old") is False


def test_kol_pump_variants():
    assert _kol_pump([{"pattern": "pump_and_dump"}]) is True
    assert _kol_pump([{"pattern": "shill_alert"}]) is True
    assert _kol_pump([{"pattern": "benign"}, "junk"]) is False
    assert _kol_pump([]) is False
    assert _kol_pump({"kol_pump": True}) is True
    assert _kol_pump({"pattern": "shill"}) is True
    assert _kol_pump({"pattern": "benign"}) is False
    assert _kol_pump("pump promo") is True
    assert _kol_pump("silent") is False


def test_coordinated_shill_variants():
    assert _coordinated_shill({"coordinated_shill": True}) is True
    assert _coordinated_shill({"coordinated": False}) is False
    assert _coordinated_shill({"alerts": [{"a": 1}]}) is True
    assert _coordinated_shill({"shill_alerts": []}) is False
    assert _coordinated_shill({"alerts": 3}) is True
    assert _coordinated_shill({}) is False
    assert _coordinated_shill("coordinated shill") is True
    assert _coordinated_shill("quiet") is False


def test_smart_money_exit_variants():
    assert _smart_money_exit(-5) is True
    assert _smart_money_exit(5) is False
    assert _smart_money_exit({"smart_money_sold": True}) is True
    assert _smart_money_exit({"smart_money_exit": True}) is True
    assert _smart_money_exit({"net_bought": -3}) is True
    assert _smart_money_exit({"buy_pressure": 3}) is False
    assert _smart_money_exit({"net": 0}) is False
    assert _smart_money_exit({"sold": True}) is True
    assert _smart_money_exit({}) is False
    assert _smart_money_exit("exiting") is True
    assert _smart_money_exit("sold") is True


def test_kol_verified_variants():
    assert _kol_verified(True) is True
    assert _kol_verified(False) is False
    assert _kol_verified("") is False
    assert _kol_verified("alice") is True
    assert _kol_verified({"kol_handle": "@alice"}) is True
    assert _kol_verified({"verified": True}) is True
    assert _kol_verified({"verified": False}) is False
    assert _kol_verified({"kol_handle": ""}) is False
    assert _kol_verified({}) is False
    assert _kol_verified(None) is False


def test_signal_boolean_wrappers():
    assert _lp_removed({"lp_removed": True}) is True
    assert _bundle_suspect({"bundled": True}) is True
    assert _rug_history({"has_rugged": True}) is True
    assert _bad_funder({"mixer_funded": True}) is True
    assert _lp_removed("no") is False
    assert _bundle_suspect(None) is False


def test_rug_risk_score_verdict_thresholds():
    assert rug_risk_score({"honeypot": True})["verdict"] == "critical"
    high = rug_risk_score({"bundled": True, "wash_trading": True, "freezable": True})
    assert high["verdict"] == "high"
    med = rug_risk_score({"age_seconds": 300, "pool_count": 1, "liquidity": 5000})
    assert med["verdict"] == "medium"
    assert rug_risk_score({"pool_count": 1})["verdict"] == "low"


def test_analysis_text_tolerates_malformed_items():
    result = {
        "score": 50,
        "verdict": "high",
        "analysis": ["junk", {"signal": "honeypot", "weight": "40", "detail": "x"}, None],
    }
    text = risk_analysis_text(result)
    assert "CONTRACT: honeypot (+40)" in text
    # Malformed entries are skipped, not fatal.
    assert "Risk Score: 50/100 — HIGH" in text


def test_analysis_text_non_dict_result():
    assert risk_analysis_text("garbage").startswith("Risk Score: 0/100 — LOW")
    assert risk_analysis_text(None).startswith("Risk Score: 0/100 — LOW")


def test_verdict_text_high_and_medium():
    text = risk_verdict_text({"score": 60, "verdict": "high", "flags": ["mintable", "freezable"]})
    assert "🟠 HIGH (60/100) — mintable · freezable" in text
    text2 = risk_verdict_text({"score": 30, "verdict": "medium", "flags": ["single_pool"]})
    assert "🟡 MEDIUM (30/100)" in text2


def test_verdict_text_unknown_verdict_defaults_emoji():
    text = risk_verdict_text({"score": 90, "verdict": "unknown", "flags": []})
    assert "🔴 UNKNOWN (90/100)" in text
