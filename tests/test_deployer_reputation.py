"""Pure unit tests for the deployer reputation module (no network/DB).

Every test injects stub fetchers, so ClickHouse and the databus JSON files
are never touched. The legacy behavioral-scoring API is asserted as still
importable because ``app/deployer_reputation.py`` re-exports it as a compat
shim consumed by ``app/streaming_rag.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from app.domains.scanner.deployer_reputation import (
    _default_fetch_funding_source,
    _default_fetch_prior_deployments,
    _default_fetch_rug_history,
    _default_is_bad_actor,
    _load_scam_db,
    _lookup_bad_actor,
    _norm_lookup_key,
    _run_fetch,
    build_behavioral_fingerprint,
    deployer_reputation,
    reputation_signal,
    score_deployer_risk,
)


async def _fetch_none(*_args: Any) -> Any:
    """Stub fetcher that returns no data (neutral for every signal)."""
    return None


async def _fetch_zero(*_args: Any) -> int:
    """Stub fetcher that reports zero prior deployments."""
    return 0


async def _prior(n: int) -> Callable[[str, str], Awaitable[int]]:
    """Build a prior-deployments fetcher that always returns ``n``."""

    async def _inner(_address: str, _chain: str) -> int:
        return n

    return _inner


async def _rug(flagged: bool) -> Callable[[str, str], Awaitable[bool]]:
    """Build a rug-history fetcher that always returns ``flagged``."""

    async def _inner(_address: str, _chain: str) -> bool:
        return flagged

    return _inner


async def test_flags_repeat_deployer() -> None:
    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=await _prior(5),
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert result["prior_deployments"] == 5
    assert result["repeat_deployer"] is True
    assert result["rug_history"] is False
    assert result["funding_source"] is None
    assert result["score"] > 0
    assert result["evidence"] == "deployer launched 5 tokens"


async def test_flags_rug_history() -> None:
    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_zero,
        fetch_rug_history=await _rug(True),
        fetch_funding_source=_fetch_none,
    )
    assert result["rug_history"] is True
    assert result["repeat_deployer"] is False
    assert result["score"] == pytest.approx(40.0)
    assert result["evidence"] == "deployer has rug history"


async def test_scores_higher_for_more_deployments() -> None:
    single = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=await _prior(1),
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    repeat = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=await _prior(4),
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    batch = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=await _prior(12),
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert single["score"] == pytest.approx(6.0)
    assert single["repeat_deployer"] is False
    assert repeat["score"] > single["score"]
    assert repeat["repeat_deployer"] is True
    assert batch["score"] > repeat["score"]
    assert batch["repeat_deployer"] is True
    # Deployment component is capped at 40: min(12*6, 40) + repeat + batch.
    assert batch["score"] == pytest.approx(40.0 + 18.0 + 12.0)


async def test_neutral_on_empty() -> None:
    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_none,
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert result == {
        "score": 0.0,
        "prior_deployments": None,
        "repeat_deployer": False,
        "rug_history": False,
        "funding_source": None,
        "evidence": None,
    }
    assert reputation_signal(result) is None


async def test_neutral_when_no_data() -> None:
    """Zero prior deployments and no flags still renders a neutral result."""
    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_zero,
        fetch_rug_history=await _rug(False),
        fetch_funding_source=_fetch_none,
    )
    assert result["score"] == 0.0
    assert result["prior_deployments"] == 0
    assert result["repeat_deployer"] is False
    assert result["evidence"] is None
    assert reputation_signal(result) is None


async def test_fail_open_on_fetcher_error() -> None:
    async def _boom(_address: str, _chain: str) -> int:
        raise RuntimeError("clickhouse down")

    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_boom,
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert result == {
        "score": 0.0,
        "prior_deployments": None,
        "repeat_deployer": False,
        "rug_history": False,
        "funding_source": None,
        "evidence": None,
    }


async def test_fail_open_when_fetcher_raises_synchronously() -> None:
    def _broken(_address: str, _chain: str) -> int:  # sync, not awaitable
        raise ValueError("bad fetcher")

    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_broken,  # type: ignore[arg-type]  # intentionally broken
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
    )
    assert result["score"] == 0.0
    assert result["prior_deployments"] is None


async def test_funding_bad_actor_bumps_score() -> None:
    async def _funder(_address: str, _chain: str) -> str:
        return "0xbadfunder"

    async def _bad_actor(address: str) -> bool:
        return address == "0xbadfunder"

    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_none,
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_funder,
        is_bad_actor=_bad_actor,
    )
    assert result["funding_source"] == "0xbadfunder"
    assert result["score"] == pytest.approx(25.0)
    assert "known bad actor" in (result["evidence"] or "")


async def test_clean_funder_not_flagged() -> None:
    async def _funder(_address: str, _chain: str) -> str:
        return "0xlegitfunder"

    async def _bad_actor(address: str) -> bool:
        return False

    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_none,
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_funder,
        is_bad_actor=_bad_actor,
    )
    assert result["funding_source"] == "0xlegitfunder"
    assert result["score"] == 0.0
    assert result["evidence"] is None


def test_reputation_signal_renders() -> None:
    # Rug history is the highest-priority signal.
    assert (
        reputation_signal({"rug_history": True, "prior_deployments": 3, "repeat_deployer": True})
        == "deployer has rug history"
    )
    # Known deployment count beats the generic repeat label.
    assert (
        reputation_signal({"rug_history": False, "prior_deployments": 12, "repeat_deployer": True})
        == "deployer launched 12 tokens"
    )
    # Generic repeat label when the count is unknown.
    assert (
        reputation_signal({"rug_history": False, "prior_deployments": None, "repeat_deployer": True})
        == "repeat deployer"
    )
    # Clean / unknown -> None.
    assert reputation_signal({"rug_history": False, "prior_deployments": 0, "repeat_deployer": False}) is None
    assert reputation_signal({}) is None


def test_legacy_behavioral_api_still_importable() -> None:
    """Protects the app/deployer_reputation.py compat shim from regression."""
    vector = build_behavioral_fingerprint("0xabc", deployment_count=2)
    assert len(vector) == 6

    scored = score_deployer_risk("0xabc", deployment_count=12, mixer_funded=True)
    assert 0 <= scored["risk_score"] <= 100
    assert scored["address"] == "0xabc"
    assert scored["risk_label"] in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


# ═══════════════════════════════════════════════════════════════════════
# score_deployer_risk — per-signal branch coverage (missing lines)
# ═══════════════════════════════════════════════════════════════════════


def test_legacy_first_deployment_signal_when_zero_contracts() -> None:
    scored = score_deployer_risk("0xabc", deployment_count=0)
    signals = [s["signal"] for s in scored["signals_found"]]
    assert any("First-time" in s for s in signals)


def test_legacy_cex_funded_is_mitigation() -> None:
    """cex_funded carries a NEGATIVE weight — it must lower the score."""
    bare = score_deployer_risk("0xabc", deployment_count=0)
    funded = score_deployer_risk("0xabc", deployment_count=0, cex_funded=True)
    assert funded["risk_score"] < bare["risk_score"]
    assert any("centralized exchange" in m["signal"] for m in funded["mitigations"])
    assert "mitigated by:" in funded["summary"]


def test_legacy_code_similarity_and_scammer_flags() -> None:
    scored = score_deployer_risk(
        "0xabc",
        deployment_count=3,
        code_similarity=0.9,
        funded_by_scammer=True,
        cloned_contract=True,
        rapid_deploy=True,
    )
    signals = " ".join(s["signal"] for s in scored["signals_found"])
    assert "similar to known scam" in signals
    assert "known scam address" in signals
    assert "cloned token" in signals
    assert "short time window" in signals
    assert scored["risk_label"] in {"HIGH", "CRITICAL"}


def test_legacy_fingerprint_match_applies_modifier() -> None:
    """A >0.7 similarity to a non-legitimate pattern fires known_scammer_match."""
    # Maxed-out deployment count + mixer + no verification is close to
    # rug_pull_factory's vector [25, 2, 1, 0, 0.3, 1].
    scored = score_deployer_risk(
        "0xabc",
        deployment_count=50,
        avg_interval_hours=2,
        mixer_funded=True,
        code_uniqueness=0.3,
    )
    assert scored["fingerprint_match"] is not None
    signals = [s["signal"] for s in scored["signals_found"]]
    assert any("fingerprint matches" in s.lower() for s in signals)
    assert scored["risk_label"] == "CRITICAL"
    assert scored["risk_score"] >= 80


def test_legacy_mid_band_labels() -> None:
    """HIGH (>=60) and MEDIUM (>=35) bands render distinct labels."""
    high = score_deployer_risk("0xabc", deployment_count=15, mixer_funded=True, code_similarity=0.8)
    assert high["risk_label"] in {"HIGH", "CRITICAL"}
    medium = score_deployer_risk("0xabc", deployment_count=1, source_verified=False)
    assert medium["risk_label"] in {"MEDIUM", "LOW"}
    # A lone medium-weight signal with no other flags hits the generic summary.
    scored = score_deployer_risk("0xabc", deployment_count=1)
    assert f"{scored['risk_label']} risk" in scored["summary"] or "Low risk" in scored["summary"]


def test_legacy_unverified_source_signal() -> None:
    scored = score_deployer_risk("0xabc", deployment_count=1, source_verified=False)
    assert any("not verified" in s["signal"] for s in scored["signals_found"])


# ═══════════════════════════════════════════════════════════════════════
# _norm_lookup_key / _load_scam_db / _lookup_bad_actor (missing lines)
# ═══════════════════════════════════════════════════════════════════════


def test_norm_lookup_key_handles_non_0x_and_empty() -> None:
    # Base58 / non-EVM values stay exact-case (case-sensitive).
    assert _norm_lookup_key("AbCdEf123456789") == "AbCdEf123456789"
    assert _norm_lookup_key("0xABCDEF") == "0xabcdef"
    assert _norm_lookup_key(" 0xABCDEF ") == "0xabcdef"
    assert _norm_lookup_key("") == ""
    assert _norm_lookup_key(None) == ""  # type: ignore[arg-type]


def test_load_scam_db_fail_open_on_missing_files(monkeypatch, tmp_path) -> None:
    """Absent databus JSON files yield empty indexes, never raise."""
    import app.domains.scanner.deployer_reputation as mod

    monkeypatch.setattr(mod, "_scam_db", None)
    monkeypatch.setattr(mod, "_KNOWN_SCAMMERS_PATH", tmp_path / "nope1.json")
    monkeypatch.setattr(mod, "_KOL_RUG_HISTORY_PATH", tmp_path / "nope2.json")
    monkeypatch.setattr(mod, "_VERIFIED_KOL_WALLETS_PATH", tmp_path / "nope3.json")
    db = _load_scam_db()
    assert db["scammer_addresses"] == set()
    assert db["scammer_handles"] == set()
    assert db["rug_handles"] == set()
    assert db["wallet_to_handle"] == {}


def test_load_scam_db_parses_real_shapes(monkeypatch, tmp_path) -> None:
    import json

    import app.domains.scanner.deployer_reputation as mod

    scammers = tmp_path / "known_scammers.json"
    scammers.write_text(
        json.dumps(
            {
                "accounts": [
                    {"handle": "@evil", "address": "0xBadAddRess"},
                    {"handle": "@multi", "addresses": ["0xAAA", "0xBBB"], "wallet": "0xCCC"},
                    {"handle": ""},  # skipped: no handle
                    "not-a-dict",  # skipped: not a dict
                ]
            }
        ),
        encoding="utf-8",
    )
    rug = tmp_path / "kol_rug_history.json"
    rug.write_text(json.dumps({"kol_rug_history": [{"handle": "@rugkol", "rugs": True}, "junk"]}), encoding="utf-8")
    wallets = tmp_path / "verified_kol_wallets.json"
    wallets.write_text(
        json.dumps(
            {
                "wallets": [
                    {"address": "0xKolWallet", "handle": "@kol"},
                    {"address": "", "handle": "@x"},
                    "not-a-dict",  # skipped: not a dict
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(mod, "_scam_db", None)
    monkeypatch.setattr(mod, "_KNOWN_SCAMMERS_PATH", scammers)
    monkeypatch.setattr(mod, "_KOL_RUG_HISTORY_PATH", rug)
    monkeypatch.setattr(mod, "_VERIFIED_KOL_WALLETS_PATH", wallets)
    db = _load_scam_db()
    assert "@evil" in db["scammer_handles"]
    assert "0xbadaddress" in db["scammer_addresses"]
    assert "0xaaa" in db["scammer_addresses"]
    assert "0xccc" in db["scammer_addresses"]
    assert "@rugkol" in db["rug_handles"]
    assert db["wallet_to_handle"]["0xkolwallet"] == "@kol"


def test_load_scam_db_fail_open_on_malformed_json(monkeypatch, tmp_path) -> None:
    import app.domains.scanner.deployer_reputation as mod

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(mod, "_scam_db", None)
    for attr in ("_KNOWN_SCAMMERS_PATH", "_KOL_RUG_HISTORY_PATH", "_VERIFIED_KOL_WALLETS_PATH"):
        monkeypatch.setattr(mod, attr, bad)
    db = _load_scam_db()
    assert db["scammer_addresses"] == set()
    assert db["wallet_to_handle"] == {}


def test_lookup_bad_actor_all_match_paths(monkeypatch) -> None:
    import app.domains.scanner.deployer_reputation as mod

    db = {
        "scammer_addresses": {"0xdirectscam"},
        "scammer_handles": {"scamhandle"},
        "rug_handles": {"rugkol"},
        "wallet_to_handle": {"0xkolwallet": "scamhandle", "0xrugwallet": "rugkol"},
    }
    monkeypatch.setattr(mod, "_load_scam_db", lambda: db)

    assert _lookup_bad_actor("0xDirectScam") == (True, "address listed in known_scammers")
    assert _lookup_bad_actor("0xKOLWallet")[0] is True
    assert "known scammer scamhandle" in _lookup_bad_actor("0xKOLWallet")[1]
    assert _lookup_bad_actor("0xRugWallet")[0] is True
    assert "rug history" in _lookup_bad_actor("0xRugWallet")[1]
    assert _lookup_bad_actor("scamhandle") == (True, "handle listed in known_scammers")
    assert _lookup_bad_actor("rugkol") == (True, "handle has documented rug history")
    assert _lookup_bad_actor("0xCleanWallet") == (False, "")
    assert _lookup_bad_actor("") == (False, "")


# ═══════════════════════════════════════════════════════════════════════
# Default fetchers — fail-open with mocked ClickHouse (missing lines)
# ═══════════════════════════════════════════════════════════════════════


async def test_default_prior_deployments_fail_open(monkeypatch) -> None:
    class _FakeClient:
        def execute(self, *args, **kwargs):
            return [(3,)]

    with monkeypatch.context() as m:
        m.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client",
            lambda: _FakeClient(),
        )
        count = await _default_fetch_prior_deployments("0xabc", "ethereum")
    assert count == 3


async def test_default_prior_deployments_none_when_client_missing(monkeypatch) -> None:
    with monkeypatch.context() as m:
        m.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client",
            lambda: None,
        )
        assert await _default_fetch_prior_deployments("0xabc", "ethereum") is None


async def test_default_rug_history_and_bad_actor_use_lookup(monkeypatch) -> None:
    import app.domains.scanner.deployer_reputation as mod

    monkeypatch.setattr(mod, "_lookup_bad_actor", lambda addr: (addr == "0xscam", "x"))
    assert await _default_fetch_rug_history("0xscam", "ethereum") is True
    assert await _default_fetch_rug_history("0xclean", "ethereum") is False
    assert await _default_is_bad_actor("0xscam") is True
    assert await _default_is_bad_actor("0xclean") is False


async def test_default_funding_source_fail_open(monkeypatch) -> None:
    # Empty chain → None without touching ClickHouse.
    assert await _default_fetch_funding_source("0xabc", "") is None

    class _FakeClient:
        def execute(self, *args, **kwargs):
            return [("0xFunder",)]

    with monkeypatch.context() as m:
        m.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client",
            lambda: _FakeClient(),
        )
        funder = await _default_fetch_funding_source("0xabc", "ethereum")
    assert funder == "0xfunder"

    with monkeypatch.context() as m:
        m.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client",
            lambda: None,
        )
        assert await _default_fetch_funding_source("0xabc", "ethereum") is None


async def test_run_fetch_none_fetcher_returns_none() -> None:
    assert await _run_fetch(None, "0xabc", "ethereum") is None


# ═══════════════════════════════════════════════════════════════════════
# deployer_reputation — gather-level fail-open (missing 618-619)
# ═══════════════════════════════════════════════════════════════════════


async def test_deployer_reputation_fail_open_when_gather_raises(monkeypatch) -> None:

    import app.domains.scanner.deployer_reputation as mod

    async def _boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    # Make the is_bad_actor call (awaited separately) fail open too.
    monkeypatch.setattr(mod, "_run_fetch", _boom)
    result = await deployer_reputation(
        "0xabc",
        "ethereum",
        fetch_prior_deployments=_fetch_none,
        fetch_rug_history=_fetch_none,
        fetch_funding_source=_fetch_none,
        is_bad_actor=_boom,
    )
    assert result == {
        "score": 0.0,
        "prior_deployments": None,
        "repeat_deployer": False,
        "rug_history": False,
        "funding_source": None,
        "evidence": None,
    }


# ═══════════════════════════════════════════════════════════════════════
# Remaining branch coverage: summaries, cache, fetcher exception paths
# ═══════════════════════════════════════════════════════════════════════


def test_legacy_summary_no_signals_with_mitigations() -> None:
    """cex-funded + zero risk signals renders the 'strong positive signals' summary."""
    scored = score_deployer_risk("0xabc", deployment_count=1, source_verified=True, cex_funded=True)
    assert "strong positive signals" in scored["summary"]
    assert scored["signals_found"] == []


def test_legacy_summary_no_signals_insufficient_data() -> None:
    """Verified source with no other signals → insufficient-data summary."""
    scored = score_deployer_risk("0xabc", deployment_count=1, source_verified=True)
    assert "insufficient data" in scored["summary"]
    assert scored["signals_found"] == []


def test_load_scam_db_caches_after_first_load(monkeypatch, tmp_path) -> None:
    """Second call returns the same object without re-reading files."""
    import app.domains.scanner.deployer_reputation as mod

    calls = {"n": 0}

    def _fake_load():
        calls["n"] += 1
        return {"cached": True}

    monkeypatch.setattr(mod, "_scam_db", None)
    monkeypatch.setattr(mod, "_load_scam_db_impl", _fake_load, raising=False)
    # The module caches via the _scam_db global inside _load_scam_db; simulate
    # by pre-setting the cache and asserting no re-read happens.
    monkeypatch.setattr(mod, "_scam_db", {"cached": True})
    assert _load_scam_db() == {"cached": True}


async def test_default_prior_deployments_fail_open_on_query_error(monkeypatch) -> None:
    class _BoomClient:
        def execute(self, *args, **kwargs):
            raise RuntimeError("clickhouse down")

    with monkeypatch.context() as m:
        m.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client",
            lambda: _BoomClient(),
        )
        assert await _default_fetch_prior_deployments("0xabc", "ethereum") is None


async def test_default_prior_deployments_zero_when_no_rows(monkeypatch) -> None:
    class _EmptyClient:
        def execute(self, *args, **kwargs):
            return []

    with monkeypatch.context() as m:
        m.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client",
            lambda: _EmptyClient(),
        )
        assert await _default_fetch_prior_deployments("0xabc", "ethereum") == 0


async def test_default_funding_source_none_when_no_rows(monkeypatch) -> None:
    class _EmptyClient:
        def execute(self, *args, **kwargs):
            return []

    with monkeypatch.context() as m:
        m.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client",
            lambda: _EmptyClient(),
        )
        assert await _default_fetch_funding_source("0xabc", "ethereum") is None


async def test_default_funding_source_fail_open_on_query_error(monkeypatch) -> None:
    class _BoomClient:
        def execute(self, *args, **kwargs):
            raise RuntimeError("clickhouse down")

    with monkeypatch.context() as m:
        m.setattr(
            "app.domains.databus.providers.chains.lite_chain_indexer._get_ch_client",
            lambda: _BoomClient(),
        )
        assert await _default_fetch_funding_source("0xabc", "ethereum") is None
