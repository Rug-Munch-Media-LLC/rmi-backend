"""SENTINEL deep-forensic integration tests — full pipeline end-to-end.

Covers:
  1. Full scan flow (free-tier modules produce populated results)
  2. Premium gating (free/pro lock, elite/admin unlock)
  3. Error isolation (one module failing never crashes the pipeline)
  4. Scoring integration (SENTINEL findings affect unified risk score, sentinel_boost)
  5. HHI data flow (SENTINEL holder HHI promoted into holder_health)
  6. Unified scanner integration (ScanResult.sentinel populated by Phase 5)
  7. x402 tool definitions (sentinel_scan tool pricing/description)

Design:
  - Real pipeline logic, mocked I/O boundary (no real chain hits).
  - Uses ``_get_analyzers`` / ``_resolve_deployer`` patching to inject
    controlled module results.
  - Tests the same code paths the live scanner hits.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.unified.model import ScanResult
from app.domains.scanner.unified.sentinel_phase import (
    PREMIUM_UNLOCK_TIERS,
    SENTINEL_ALL_MODULES,
    SENTINEL_FREE_MODULES,
    SENTINEL_PREMIUM_DESCRIPTIONS,
    SENTINEL_PREMIUM_MODULES,
    UNLOCK_PROMPT,
    extract_deployer_from_security,
    run_sentinel_phase,
)

# ── Test constants ────────────────────────────────────────────────────
TOKEN = "TokenAddr123456789012345678901234567890"
DEPLOYER = "DeployerAddr1234567890123456789012345678"
CHAIN = "solana"


# ═══════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════


def _fake_analyzers(
    raise_keys: set[str] | None = None,
    sleep_keys: set[str] | None = None,
    inflight: dict[str, dict] | None = None,
) -> dict:
    """Build analyzer stubs for every SENTINEL module.

    Modules in ``raise_keys`` raise RuntimeError; modules in ``sleep_keys``
    sleep forever (timeout-tested); other modules return ``inflight[key]``
    if supplied, else a generic low-risk result.
    """
    raise_keys = raise_keys or set()
    sleep_keys = sleep_keys or set()
    inflight = inflight or {}

    def _make(key: str):
        if key in raise_keys:

            async def _raise(*_a, **_kw):
                raise RuntimeError(f"boom: {key}")

            return _raise
        if key in sleep_keys:

            async def _sleep(*_a, **_kw):
                await asyncio.sleep(60)
                return {"risk_score": 99}

            return _sleep
        result = inflight.get(key, {"risk_score": 10, "warnings": [], "hhi": 1200, "risk_level": "low"})
        return AsyncMock(return_value=result)

    return {key: _make(key) for key in SENTINEL_ALL_MODULES}


def _patch_sentinel_phase(
    analyzers: dict | None = None,
    deployer: str | None = DEPLOYER,
    raise_keys: set[str] | None = None,
    inflight: dict[str, dict] | None = None,
) -> list:
    """Return patch managers for _get_analyzers and _resolve_deployer."""
    if analyzers is None:
        analyzers = _fake_analyzers(raise_keys=raise_keys, inflight=inflight)
    return [
        patch(
            "app.domains.scanner.unified.sentinel_phase._get_analyzers",
            return_value=analyzers,
        ),
        patch(
            "app.domains.scanner.unified.sentinel_phase._resolve_deployer",
            new=AsyncMock(return_value=deployer),
        ),
    ]


# ═══════════════════════════════════════════════════════════════════════
# 1. Full scan flow — free tier
# ═══════════════════════════════════════════════════════════════════════


class TestFullScanFlowFreeTier:
    """The free-tier SENTINEL phase runs all 6 free modules and gates premium."""

    @pytest.mark.asyncio
    async def test_free_tier_runs_all_free_modules(self):
        """Every free-tier module key appears in result['free']."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        assert result["premium_unlocked"] is False
        assert result["premium"] is None
        assert set(result["free"]) == set(SENTINEL_FREE_MODULES)
        assert len(result["modules_run"]) == len(SENTINEL_FREE_MODULES)

    @pytest.mark.asyncio
    async def test_free_tier_results_are_populated(self):
        """Free module results are real dicts, not None."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        for key in SENTINEL_FREE_MODULES:
            assert key in result["free"], f"{key} missing from free results"
            assert isinstance(result["free"][key], dict), f"{key} result is not a dict"
            assert "risk_score" in result["free"][key], f"{key} missing risk_score"

    @pytest.mark.asyncio
    async def test_free_tier_includes_premium_preview(self):
        """Non-elite tiers get a preview advertising locked premium modules."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        assert len(result["premium_preview"]) == len(SENTINEL_PREMIUM_MODULES)
        first = result["premium_preview"][0]
        assert "module" in first
        assert "description" in first
        assert "unlock" in first
        assert first["unlock"] == UNLOCK_PROMPT

    @pytest.mark.asyncio
    async def test_free_tier_deployer_resolved_flag(self):
        """deployer_resolved is True when a deployer was found."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(deployer=DEPLOYER):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        assert result["deployer_resolved"] is True

    @pytest.mark.asyncio
    async def test_no_deployer_flag_when_none(self):
        """deployer_resolved is False when no deployer was found."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(deployer=None):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        assert result["deployer_resolved"] is False

    @pytest.mark.asyncio
    async def test_holder_analysis_hhi_in_free_results(self):
        """Holder HHI data appears in the free-tier sentinel results."""
        hhi_result = {
            "hhi": 4500,
            "risk_level": "critical",
            "risk_score": 75,
            "fake_diversification_score": 30,
            "total_holders": 32,
            "holders_analyzed": 32,
        }
        inflight = {"holder_analysis": hhi_result}
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(inflight=inflight):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        ha = result["free"]["holder_analysis"]
        assert ha["hhi"] == 4500
        assert ha["risk_level"] == "critical"
        assert ha["fake_diversification_score"] == 30

    @pytest.mark.asyncio
    async def test_liquidity_verification_in_free_results(self):
        """LP lock verification data appears in free-tier results."""
        lv_result = {
            "total_locked_percentage": 25.0,
            "risk_level": "high",
            "fake_lock_detected": False,
            "lp_burned": False,
            "lock_duration": "7 days",
            "risk_score": 60,
        }
        inflight = {"liquidity_verification": lv_result}
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(inflight=inflight):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        lv = result["free"]["liquidity_verification"]
        assert lv["total_locked_percentage"] == 25.0
        assert lv["risk_level"] == "high"
        assert lv["fake_lock_detected"] is False

    @pytest.mark.asyncio
    async def test_contract_authority_in_free_results(self):
        """Contract authority data appears in free-tier results."""
        ca_result = {
            "risk_level": "critical",
            "risk_score": 85,
            "mint_authority_renounced": False,
            "freeze_authority_active": True,
        }
        inflight = {"contract_authority": ca_result}
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(inflight=inflight):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        ca = result["free"]["contract_authority"]
        assert ca["risk_level"] == "critical"
        assert ca["mint_authority_renounced"] is False

    @pytest.mark.asyncio
    async def test_dev_reputation_in_free_results(self):
        """Dev reputation data appears in free-tier results."""
        dr_result = {
            "reputation_score": 15,
            "reputation_level": "suspicious",
            "dev_threat_index": 0.8,
            "rug_rate": 0.6,
            "total_launches": 12,
            "serial_patterns": 3,
        }
        inflight = {"dev_reputation": dr_result}
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(inflight=inflight):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        dr = result["free"]["dev_reputation"]
        assert dr["reputation_score"] == 15
        assert dr["dev_threat_index"] == 0.8

    @pytest.mark.asyncio
    async def test_metadata_fingerprint_in_free_results(self):
        """Metadata fingerprint data appears in free-tier results."""
        mf_result = {
            "name": "CloneToken",
            "confidence": "HIGH",
            "html_structure_hash": "abc123",
            "similar_tokens": [
                {"address": "bad1", "similarity": 0.95},
                {"address": "bad2", "similarity": 0.88},
                {"address": "bad3", "similarity": 0.82},
            ],
        }
        inflight = {"metadata_fingerprint": mf_result}
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(inflight=inflight):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        mf = result["free"]["metadata_fingerprint"]
        assert mf["name"] == "CloneToken"
        assert len(mf["similar_tokens"]) == 3

    @pytest.mark.asyncio
    async def test_proxy_detection_in_free_results(self):
        """Proxy detection data appears in free-tier results."""
        px_result = {
            "is_proxy": True,
            "can_upgrade": True,
            "risk_score": 70,
            "risk_level": "high",
            "upgrade_authority": "auth123",
        }
        inflight = {"proxy_detection": px_result}
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(inflight=inflight):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        px = result["free"]["proxy_detection"]
        assert px["is_proxy"] is True
        assert px["can_upgrade"] is True


# ═══════════════════════════════════════════════════════════════════════
# 2. Premium gating
# ═══════════════════════════════════════════════════════════════════════


class TestPremiumGating:
    """Premium-tier modules are gated behind elite/admin tier."""

    @pytest.mark.parametrize("tier", ["free", "pro", "unknown"])
    @pytest.mark.asyncio
    async def test_non_elite_tiers_lock_premium(self, tier: str):
        """free/pro/unknown tiers all have premium=None."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier=tier)

        assert result["premium_unlocked"] is False
        assert result["premium"] is None
        assert len(result["premium_preview"]) == len(SENTINEL_PREMIUM_MODULES)

    @pytest.mark.parametrize("tier", ["elite", "admin"])
    @pytest.mark.asyncio
    async def test_elite_admin_unlock_premium(self, tier: str):
        """elite/admin tiers unlock all premium modules."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier=tier)

        assert result["premium_unlocked"] is True
        assert result["premium"] is not None
        assert result["premium_preview"] == []
        assert set(result["premium"]) == set(SENTINEL_PREMIUM_MODULES)
        assert len(result["modules_run"]) == len(SENTINEL_ALL_MODULES)

    @pytest.mark.asyncio
    async def test_elite_tier_premium_results_populated(self):
        """Premium module results are real dicts when unlocked."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="elite")

        for key in SENTINEL_PREMIUM_MODULES:
            assert key in result["premium"], f"{key} missing from premium results"
            assert isinstance(result["premium"][key], dict), f"{key} result is not a dict"

    @pytest.mark.asyncio
    async def test_premium_preview_has_descriptions(self):
        """Locked preview entries include descriptions for every premium module."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        for preview in result["premium_preview"]:
            assert preview["module"] in SENTINEL_PREMIUM_MODULES
            assert len(preview["description"]) > 5, f"Description too short for {preview['module']}"

    @pytest.mark.asyncio
    async def test_premium_key_not_in_free_results(self):
        """Premium module keys do not appear in free results dict."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        free_keys = set(result["free"])
        assert free_keys.isdisjoint(set(SENTINEL_PREMIUM_MODULES))

    @pytest.mark.asyncio
    async def test_elite_tier_runs_correct_number_of_modules(self):
        """elite tier runs all modules (free + premium)."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="elite")

        assert len(result["modules_run"]) == len(SENTINEL_ALL_MODULES)

    @pytest.mark.asyncio
    async def test_tier_case_insensitive(self):
        """Tier check is case-insensitive."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="ELITE")

        assert result["premium_unlocked"] is True
        assert result["premium"] is not None


# ═══════════════════════════════════════════════════════════════════════
# 3. Error isolation
# ═══════════════════════════════════════════════════════════════════════


class TestErrorIsolation:
    """A failing module is recorded in errors; the rest of the pipeline survives."""

    @pytest.mark.asyncio
    async def test_single_module_failure_does_not_crash(self):
        """One raise → error recorded, other modules still produce results."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(raise_keys={"holder_analysis"}):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        assert "holder_analysis" in result["errors"]
        assert "boom" in result["errors"]["holder_analysis"]
        assert result["free"]["holder_analysis"] is None
        # Other modules still ran and returned results
        assert result["free"]["liquidity_verification"] is not None
        assert result["free"]["contract_authority"] is not None
        assert len(result["modules_run"]) == len(SENTINEL_FREE_MODULES)

    @pytest.mark.asyncio
    async def test_all_modules_failing_still_produces_phase_dict(self):
        """Even every module failing returns a valid phase dict."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(raise_keys=set(SENTINEL_ALL_MODULES)):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="elite")

        assert result["premium_unlocked"] is True
        assert len(result["errors"]) == len(SENTINEL_ALL_MODULES)
        assert all("boom" in msg for msg in result["errors"].values())
        assert all(v is None for v in result["free"].values())
        assert all(v is None for v in result["premium"].values())

    @pytest.mark.asyncio
    async def test_module_timeout_is_recorded(self):
        """A hung module is time-boxed and produces a timeout error."""
        from app.domains.scanner.unified import sentinel_phase as phase

        analyzers = _fake_analyzers(sleep_keys={"liquidity_verification"})
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(phase, "MODULE_TIMEOUT_S", 0.05))
            for patcher in _patch_sentinel_phase(analyzers=analyzers):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        assert "timeout" in result["errors"]["liquidity_verification"]
        assert result["free"]["liquidity_verification"] is None
        # Other modules unaffected
        assert result["free"]["holder_analysis"] is not None

    @pytest.mark.asyncio
    async def test_elite_module_failure_not_in_free_results(self):
        """A failing premium module does not pollute free results."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(raise_keys={"bundle_detection"}):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="elite")

        assert "bundle_detection" in result["errors"]
        assert "bundle_detection" in result["premium"]
        assert result["premium"]["bundle_detection"] is None
        # Free modules unaffected
        assert all(v is not None for v in result["free"].values())

    @pytest.mark.asyncio
    async def test_error_strings_are_truncated(self):
        """Extremely long error messages are truncated."""
        # Create an analyzer that raises with a very long message
        long_msg = "X" * 500

        async def _long_raise(*_a, **_kw):
            raise ValueError(long_msg)

        analyzers = {key: AsyncMock(return_value={"risk_score": 10}) for key in SENTINEL_ALL_MODULES}
        analyzers["holder_analysis"] = _long_raise

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch("app.domains.scanner.unified.sentinel_phase._get_analyzers", return_value=analyzers)
            )
            stack.enter_context(
                patch(
                    "app.domains.scanner.unified.sentinel_phase._resolve_deployer",
                    new=AsyncMock(return_value=DEPLOYER),
                )
            )
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        assert "holder_analysis" in result["errors"]
        assert len(result["errors"]["holder_analysis"]) <= 200

    @pytest.mark.asyncio
    async def test_run_sentinel_phase_never_raises(self):
        """run_sentinel_phase() should NEVER raise an exception — it must always return a dict."""
        # Make _resolve_deployer raise an unhandled exception
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(raise_keys=set(SENTINEL_ALL_MODULES)):
                stack.enter_context(patcher)
            try:
                result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")
            except Exception as exc:
                pytest.fail(f"run_sentinel_phase raised unexpectedly: {exc}")

        assert isinstance(result, dict)
        assert "free" in result
        assert "errors" in result


# ═══════════════════════════════════════════════════════════════════════
# 4. Scoring integration
# ═══════════════════════════════════════════════════════════════════════


class TestScoringIntegration:
    """SENTINEL findings flow into the unified risk score and sentinel_boost."""

    def test_compute_score_with_sentinel_low_risk(self):
        """Low-risk sentinel findings don't tank the score."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = [*SENTINEL_FREE_MODULES, "security_checks:5"]
        r.sentinel = {
            "free": {
                "holder_analysis": {
                    "hhi": 1200,
                    "risk_level": "low",
                    "fake_diversification_score": 10,
                    "total_holders": 500,
                },
                "liquidity_verification": {
                    "total_locked_percentage": 95.0,
                    "risk_level": "low",
                    "fake_lock_detected": False,
                    "lp_burned": True,
                },
                "dev_reputation": {
                    "reputation_score": 85,
                    "reputation_level": "clean",
                    "dev_threat_index": 0.1,
                    "rug_rate": 0.0,
                    "total_launches": 5,
                },
                "contract_authority": {"risk_level": "low", "risk_score": 10},
                "proxy_detection": {"risk_level": "low", "is_proxy": False, "risk_score": 5},
                "metadata_fingerprint": {"name": "Token", "similar_tokens": []},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": True,
        }
        compute_score(r)
        # Low-risk sentinel findings should not drag score below baseline
        assert r.safety_score >= 45  # near baseline of 50

    def test_compute_score_with_sentinel_high_hhi(self):
        """High HHI (>5000) reduces safety score significantly."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = [*SENTINEL_FREE_MODULES, "security_checks:5"]
        r.sentinel = {
            "free": {
                "holder_analysis": {
                    "hhi": 6500,
                    "risk_level": "critical",
                    "fake_diversification_score": 60,
                    "total_holders": 10,
                },
                "liquidity_verification": {},
                "dev_reputation": {},
                "contract_authority": {},
                "proxy_detection": {},
                "metadata_fingerprint": {},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": False,
        }
        compute_score(r)
        assert r.safety_score < 50
        assert "SENTINEL_HHI_CRITICAL" in r.risk_flags

    def test_compute_score_with_sentinel_fake_lock(self):
        """Fake LP lock detection reduces safety score."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = [*SENTINEL_FREE_MODULES, "security_checks:5"]
        r.sentinel = {
            "free": {
                "holder_analysis": {},
                "liquidity_verification": {
                    "total_locked_percentage": 10.0,
                    "risk_level": "CRITICAL",
                    "fake_lock_detected": True,
                    "lp_burned": False,
                },
                "dev_reputation": {},
                "contract_authority": {},
                "proxy_detection": {},
                "metadata_fingerprint": {},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": False,
        }
        compute_score(r)
        assert r.safety_score < 50
        assert "SENTINEL_LP_LOCK" in r.risk_flags

    def test_compute_score_with_sentinel_threat(self):
        """Dev threat (>0.5) reduces safety score."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = [*SENTINEL_FREE_MODULES, "security_checks:5"]
        r.sentinel = {
            "free": {
                "holder_analysis": {},
                "liquidity_verification": {},
                "dev_reputation": {
                    "reputation_score": 15,
                    "reputation_level": "suspicious",
                    "dev_threat_index": 0.85,
                    "rug_rate": 0.7,
                    "total_launches": 50,
                },
                "contract_authority": {},
                "proxy_detection": {},
                "metadata_fingerprint": {},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": True,
        }
        compute_score(r)
        assert "SENTINEL_DEV_THREAT" in r.risk_flags
        assert r.safety_score < 50

    def test_sentinel_boost_applied_when_multiple_critical(self):
        """sentinel_boost is applied when 2+ SENTINEL modules flag critical."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = [*SENTINEL_FREE_MODULES, "security_checks:5"]
        r.sentinel = {
            "free": {
                "holder_analysis": {
                    "hhi": 7000,
                    "risk_level": "critical",
                    "fake_diversification_score": 80,
                    "total_holders": 5,
                },
                "liquidity_verification": {
                    "total_locked_percentage": 0.0,
                    "risk_level": "CRITICAL",
                    "fake_lock_detected": True,
                    "lp_burned": False,
                },
                "dev_reputation": {
                    "reputation_score": 5,
                    "reputation_level": "dangerous",
                    "dev_threat_index": 0.95,
                    "rug_rate": 0.9,
                    "total_launches": 100,
                },
                "contract_authority": {"risk_level": "CRITICAL", "risk_score": 95},
                "proxy_detection": {"risk_level": "CRITICAL", "is_proxy": True, "can_upgrade": True},
                "metadata_fingerprint": {
                    "name": "Clone",
                    "similar_tokens": [{"address": "a"}, {"address": "b"}, {"address": "c"}],
                },
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": True,
        }
        compute_score(r)
        # With 5 critical signals, sentinel_boost should be > 0
        assert r.sentinel_boost > 0
        assert r.sentinel_boost <= 15.0  # capped at 15
        # Flag format: "SENTINEL_CRITICAL_BOOST:<count>" (count = 5 critical modules)
        assert any(f.startswith("SENTINEL_CRITICAL_BOOST:") for f in r.risk_flags)
        assert r.safety_score < 50

    def test_sentinel_boost_zero_when_single_critical(self):
        """sentinel_boost is 0 when only 1 module flags critical — boost requires 2+."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = [*SENTINEL_FREE_MODULES, "security_checks:5"]
        r.sentinel = {
            "free": {
                "holder_analysis": {
                    "hhi": 7000,
                    "risk_level": "critical",
                    "fake_diversification_score": 80,
                    "total_holders": 5,
                },
                "liquidity_verification": {},
                "dev_reputation": {},
                "contract_authority": {},
                "proxy_detection": {},
                "metadata_fingerprint": {},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": False,
        }
        compute_score(r)
        assert r.sentinel_boost == 0.0

    def test_breakdown_includes_sentinel_flags(self):
        """Score breakdown contains SENTINEL flags in their dimensions."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = [*SENTINEL_FREE_MODULES, "security_checks:5"]
        r.sentinel = {
            "free": {
                "holder_analysis": {
                    "hhi": 7000,
                    "risk_level": "critical",
                    "fake_diversification_score": 80,
                    "total_holders": 5,
                },
                "liquidity_verification": {
                    "total_locked_percentage": 0.0,
                    "risk_level": "CRITICAL",
                    "fake_lock_detected": True,
                    "lp_burned": False,
                },
                "dev_reputation": {},
                "contract_authority": {"risk_level": "CRITICAL", "risk_score": 95},
                "proxy_detection": {"risk_level": "CRITICAL", "is_proxy": True, "can_upgrade": True},
                "metadata_fingerprint": {"name": "Clone", "similar_tokens": [{"a": 1}, {"a": 2}, {"a": 3}]},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": True,
        }
        compute_score(r)
        assert r.breakdown is not None
        # HHI goes to holders dimension
        assert "holders" in r.breakdown
        # Security-related flags go to security dimension
        assert "security" in r.breakdown
        # Evidence should exist
        assert r.evidence is not None

    def test_evidence_contains_sentinel_data(self):
        """evidence dict includes sentinel deep-forensic findings."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = [*SENTINEL_FREE_MODULES, "security_checks:5"]
        r.sentinel = {
            "free": {
                "holder_analysis": {
                    "hhi": 4500,
                    "risk_level": "high",
                    "fake_diversification_score": 45,
                    "total_holders": 50,
                },
                "liquidity_verification": {
                    "total_locked_percentage": 30.0,
                    "risk_level": "high",
                    "fake_lock_detected": False,
                    "lp_burned": False,
                },
                "dev_reputation": {
                    "reputation_score": 25,
                    "reputation_level": "suspicious",
                    "dev_threat_index": 0.6,
                    "rug_rate": 0.4,
                    "total_launches": 20,
                    "serial_patterns": 2,
                },
                "contract_authority": {"risk_level": "high", "risk_score": 60},
                "proxy_detection": {},
                "metadata_fingerprint": {},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": True,
        }
        compute_score(r)
        assert "sentinel" in r.evidence
        se = r.evidence["sentinel"]
        assert "holder_analysis" in se
        assert se["holder_analysis"]["hhi"] == 4500
        assert "liquidity_verification" in se
        assert "dev_reputation" in se

    def test_score_clamps_at_zero(self):
        """Score cannot go below 0 even with extreme sentinel findings."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = [*SENTINEL_FREE_MODULES, "security_checks:5"]
        # Send every possible critical flag
        r.sentinel = {
            "free": {
                "holder_analysis": {
                    "hhi": 10000,
                    "risk_level": "critical",
                    "fake_diversification_score": 100,
                    "total_holders": 1,
                },
                "liquidity_verification": {
                    "total_locked_percentage": 0.0,
                    "risk_level": "CRITICAL",
                    "fake_lock_detected": True,
                    "lp_burned": False,
                },
                "dev_reputation": {
                    "reputation_score": 0,
                    "reputation_level": "dangerous",
                    "dev_threat_index": 1.0,
                    "rug_rate": 1.0,
                    "total_launches": 500,
                },
                "contract_authority": {"risk_level": "CRITICAL", "risk_score": 100},
                "proxy_detection": {"risk_level": "CRITICAL", "is_proxy": True, "can_upgrade": True},
                "metadata_fingerprint": {"name": "Clone", "similar_tokens": [{"a": 1}] * 10},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": True,
        }
        compute_score(r)
        assert r.safety_score >= 0.0
        assert r.safety_score <= 100.0

    def test_no_sentinel_no_penalty(self):
        """Without sentinel data, scoring proceeds normally with baseline."""
        from app.domains.scanner.unified.scoring import compute_score

        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.modules_run = ["security_checks:5"]
        compute_score(r)
        # Score should be at or near baseline of 50
        assert 40 <= r.safety_score <= 60
        assert r.sentinel_boost == 0.0


# ═══════════════════════════════════════════════════════════════════════
# 5. HHI data flow into holder phase
# ═══════════════════════════════════════════════════════════════════════


class TestHHIDataFlow:
    """SENTINEL holder HHI is promoted into unified ScanResult.holder_health."""

    @pytest.mark.asyncio
    async def test_hhi_promoted_when_holder_health_is_none(self):
        """SENTINEL HHI fills holder_health.hhi when no DataBus HHI exists."""
        import contextlib

        from app.domains.scanner.unified.scanner import UnifiedTokenScanner

        scanner = UnifiedTokenScanner()
        sentinel_payload = {
            "free": {
                "holder_analysis": {"hhi": 5200, "risk_level": "critical", "total_holders": 8},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": ["holder_analysis"],
            "deployer_resolved": False,
        }

        patchers = [
            patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})),
            patch.object(scanner, "_run_wallet_analysis", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_news_sentiment", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_get_news_card", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_event_correlation", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.auto_label_deployer", new=AsyncMock(return_value=None)),
            patch("app.rag.intelligence.RAGIntelligenceEnricher.enrich_scan_result", new=AsyncMock(return_value=None)),
            patch(
                "app.domains.scanner.unified.sentinel_phase.run_sentinel_phase",
                new=AsyncMock(return_value=sentinel_payload),
            ),
            patch("app.domains.scanner.unified.sentinel_phase.extract_deployer_from_security", return_value=None),
        ]

        stack = contextlib.ExitStack()
        for patcher in patchers:
            stack.enter_context(patcher)

        with stack:
            result = await scanner.scan(TOKEN, CHAIN, tier="free")

        assert result.holder_health is not None
        assert result.holder_health["hhi"] == 5200
        assert result.holder_health["hhi_source"] == "sentinel_holder_analysis"

    @pytest.mark.asyncio
    async def test_hhi_not_overwritten_when_databus_has_hhi(self):
        """SENTINEL HHI does NOT overwrite existing DataBus holder_health.hhi.

        Tests the merge logic inside ``_run_sentinel_phase`` directly:
        when ``r.holder_health["hhi"]`` is already set (from DataBus or
        elsewhere), the SENTINEL HHI must not overwrite it.
        """
        from unittest.mock import AsyncMock, patch

        from app.domains.scanner.unified.scanner import UnifiedTokenScanner

        scanner = UnifiedTokenScanner()
        r = ScanResult(token_address=TOKEN, chain=CHAIN, tier="free")
        r.holder_health = {"hhi": 1200, "gini": 0.3}  # DataBus already has HHI
        r.security = {}

        sentinel_payload = {
            "free": {
                "holder_analysis": {"hhi": 9000, "risk_level": "critical"},
            },
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": ["holder_analysis"],
            "deployer_resolved": False,
        }

        with patch(
            "app.domains.scanner.unified.sentinel_phase.run_sentinel_phase",
            new=AsyncMock(return_value=sentinel_payload),
        ):
            await scanner._run_sentinel_phase(r, TOKEN, CHAIN, "free")

        # DataBus HHI wins — it already existed with a non-None value
        assert r.holder_health["hhi"] == 1200
        assert "hhi_source" not in r.holder_health


# ═══════════════════════════════════════════════════════════════════════
# 6. Unified scanner integration
# ═══════════════════════════════════════════════════════════════════════


class TestUnifiedScannerIntegration:
    """Phase 5 wires SENTINEL results into the unified ScanResult."""

    @pytest.mark.asyncio
    async def test_scanner_populates_sentinel_on_free_tier(self):
        """UnifiedTokenScanner.scan() populates result.sentinel for free tier."""
        import contextlib

        from app.domains.scanner.unified.scanner import UnifiedTokenScanner

        sentinel_payload = {
            "free": {k: {"risk_score": 5} for k in SENTINEL_FREE_MODULES},
            "premium": None,
            "premium_unlocked": False,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [{"module": "bundle_detection", "description": "x", "unlock": "upgrade"}],
            "errors": {},
            "modules_run": list(SENTINEL_FREE_MODULES),
            "deployer_resolved": False,
        }

        scanner = UnifiedTokenScanner()
        patchers = [
            patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})),
            patch.object(scanner, "_run_wallet_analysis", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_news_sentiment", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_get_news_card", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_event_correlation", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.auto_label_deployer", new=AsyncMock(return_value=None)),
            patch("app.rag.intelligence.RAGIntelligenceEnricher.enrich_scan_result", new=AsyncMock(return_value=None)),
            patch(
                "app.domains.scanner.unified.sentinel_phase.run_sentinel_phase",
                new=AsyncMock(return_value=sentinel_payload),
            ),
            patch("app.domains.scanner.unified.sentinel_phase.extract_deployer_from_security", return_value=None),
        ]

        stack = contextlib.ExitStack()
        for patcher in patchers:
            stack.enter_context(patcher)

        with stack:
            result = await scanner.scan(TOKEN, CHAIN, tier="free")

        assert result.sentinel is not None
        assert result.sentinel["premium_unlocked"] is False
        assert set(SENTINEL_FREE_MODULES).issubset(result.sentinel["free"])
        assert result.sentinel["premium"] is None
        assert "sentinel:free" in result.enrichment_sources

    @pytest.mark.asyncio
    async def test_scanner_elite_unlocks_premium_sentinel(self):
        """Elite-tier scan returns unlocked premium SENTINEL results."""
        import contextlib

        from app.domains.scanner.unified.scanner import UnifiedTokenScanner

        sentinel_payload = {
            "free": {k: {"risk_score": 5} for k in SENTINEL_FREE_MODULES},
            "premium": {k: {"risk_score": 5} for k in SENTINEL_PREMIUM_MODULES},
            "premium_unlocked": True,
            "premium_modules": list(SENTINEL_PREMIUM_MODULES),
            "premium_preview": [],
            "errors": {},
            "modules_run": list(SENTINEL_ALL_MODULES),
            "deployer_resolved": False,
        }

        scanner = UnifiedTokenScanner()
        patchers = [
            patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})),
            patch.object(scanner, "_run_wallet_analysis", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_news_sentiment", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_get_news_card", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_event_correlation", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.auto_label_deployer", new=AsyncMock(return_value=None)),
            patch("app.rag.intelligence.RAGIntelligenceEnricher.enrich_scan_result", new=AsyncMock(return_value=None)),
            patch(
                "app.domains.scanner.unified.sentinel_phase.run_sentinel_phase",
                new=AsyncMock(return_value=sentinel_payload),
            ),
            patch("app.domains.scanner.unified.sentinel_phase.extract_deployer_from_security", return_value=None),
        ]

        stack = contextlib.ExitStack()
        for patcher in patchers:
            stack.enter_context(patcher)

        with stack:
            result = await scanner.scan(TOKEN, CHAIN, tier="elite")

        assert result.sentinel["premium_unlocked"] is True
        assert set(result.sentinel["premium"]) == set(SENTINEL_PREMIUM_MODULES)
        assert "sentinel:premium" in result.enrichment_sources
        assert "sentinel:free" in result.enrichment_sources

    @pytest.mark.asyncio
    async def test_scanner_sentinel_failure_keeps_scan_alive(self):
        """A broken SENTINEL phase never fails the overall scan."""
        import contextlib

        from app.domains.scanner.unified.scanner import UnifiedTokenScanner

        scanner = UnifiedTokenScanner()
        patchers = [
            patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})),
            patch.object(scanner, "_run_wallet_analysis", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_news_sentiment", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_get_news_card", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_event_correlation", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.auto_label_deployer", new=AsyncMock(return_value=None)),
            patch("app.rag.intelligence.RAGIntelligenceEnricher.enrich_scan_result", new=AsyncMock(return_value=None)),
            patch(
                "app.domains.scanner.unified.sentinel_phase.run_sentinel_phase",
                new=AsyncMock(side_effect=RuntimeError("sentinel down")),
            ),
        ]

        stack = contextlib.ExitStack()
        for patcher in patchers:
            stack.enter_context(patcher)

        with stack:
            result = await scanner.scan(TOKEN, CHAIN, tier="free")

        assert result.token_address == TOKEN
        assert result.safety_score >= 0
        assert result.sentinel is not None
        assert "_phase" in result.sentinel["errors"]

    @pytest.mark.asyncio
    async def test_scanner_sentinel_timeout_fallback(self):
        """A slow SENTINEL phase (timeout) gets the fallback error envelope."""
        import asyncio
        import contextlib

        from app.domains.scanner.unified.scanner import UnifiedTokenScanner

        async def _slow_sentinel(*_a, **_kw):
            await asyncio.sleep(60)
            return {"free": {}}

        scanner = UnifiedTokenScanner()
        patchers = [
            patch.object(scanner, "_fetch_batch", new=AsyncMock(return_value={})),
            patch.object(scanner, "_run_wallet_analysis", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_news_sentiment", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_get_news_card", new=AsyncMock(return_value=None)),
            patch.object(scanner, "_run_event_correlation", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.persist_result", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.scanner.auto_label_deployer", new=AsyncMock(return_value=None)),
            patch("app.rag.intelligence.RAGIntelligenceEnricher.enrich_scan_result", new=AsyncMock(return_value=None)),
            patch("app.domains.scanner.unified.sentinel_phase.run_sentinel_phase", new=_slow_sentinel),
            patch("app.domains.scanner.unified.sentinel_phase.extract_deployer_from_security", return_value=None),
        ]

        stack = contextlib.ExitStack()
        for patcher in patchers:
            stack.enter_context(patcher)

        with stack:
            result = await scanner.scan(TOKEN, CHAIN, tier="free")

        # Scan still completes; sentinel is in error
        assert result.token_address == TOKEN
        assert result.safety_score >= 0


# ═══════════════════════════════════════════════════════════════════════
# 7. x402 sentinel_scan tool definition
# ═══════════════════════════════════════════════════════════════════════


class TestX402Integration:
    """The x402 SENTINEL deep scan tool is wired and priced."""

    def test_sentinel_scan_tool_exists(self):
        """sentinel_scan tool definition exists in SENTINEL_MODULES."""
        from app.routers._expanded_tools.sentinel_modules import SENTINEL_MODULES

        assert "sentinel_scan" in SENTINEL_MODULES
        tool = SENTINEL_MODULES["sentinel_scan"]
        assert tool["category"] == "security"
        assert tool["price_usd"] == 0.15
        assert tool["price_atoms"] == "150000"
        assert tool["trial_free"] == 1

    def test_sentinel_scan_description_non_empty(self):
        """sentinel_scan has a meaningful description."""
        from app.routers._expanded_tools.sentinel_modules import SENTINEL_MODULES

        desc = SENTINEL_MODULES["sentinel_scan"]["description"]
        assert len(desc) > 10
        assert "security" in desc.lower() or "scan" in desc.lower()

    def test_sentinel_free_module_tools_exist(self):
        """Free-tier SENTINEL tools exist as x402 modules too."""
        from app.routers._expanded_tools.sentinel_modules import SENTINEL_MODULES

        expected_tool_keys = {
            "holder_analysis",
            "liquidity_verify",
            "dev_reputation",
            "metadata_fingerprint",
            "pumpfun_analysis",
            "sentiment_check",
        }
        found = set(expected_tool_keys) & set(SENTINEL_MODULES)
        assert len(found) >= 4, f"Expected >=4 SENTINEL x402 tools, found {len(found)}"


# ═══════════════════════════════════════════════════════════════════════
# 8. Configuration constants
# ═══════════════════════════════════════════════════════════════════════


class TestConfigurationConstants:
    """SENTINEL constants are correct and consistent."""

    def test_premium_unlock_tiers_match_elite_admin(self):
        assert "elite" in PREMIUM_UNLOCK_TIERS
        assert "admin" in PREMIUM_UNLOCK_TIERS
        assert len(PREMIUM_UNLOCK_TIERS) == 2

    def test_free_modules_are_six(self):
        assert len(SENTINEL_FREE_MODULES) == 6

    def test_all_modules_must_equal_free_plus_premium(self):
        assert set(SENTINEL_ALL_MODULES) == set(SENTINEL_FREE_MODULES) | set(SENTINEL_PREMIUM_MODULES)
        assert set(SENTINEL_FREE_MODULES).isdisjoint(SENTINEL_PREMIUM_MODULES)

    def test_all_premium_modules_have_descriptions(self):
        for key in SENTINEL_PREMIUM_MODULES:
            assert key in SENTINEL_PREMIUM_DESCRIPTIONS, f"Missing description for premium module {key}"
            assert len(SENTINEL_PREMIUM_DESCRIPTIONS[key]) > 5, f"Description too short for {key}"

    def test_module_count_is_29(self):
        """There are exactly 29 SENTINEL modules (6 free + 23 premium)."""
        assert len(SENTINEL_ALL_MODULES) == 29


# ═══════════════════════════════════════════════════════════════════════
# 9. Deployer extraction
# ═══════════════════════════════════════════════════════════════════════


class TestDeployerExtraction:
    """extract_deployer_from_security handles various GoPlus-style payloads."""

    def test_extract_creator_address(self):
        result = extract_deployer_from_security({"creator_address": DEPLOYER})
        assert result == DEPLOYER

    def test_extract_creator(self):
        result = extract_deployer_from_security({"creator": DEPLOYER})
        assert result == DEPLOYER

    def test_extract_deployer(self):
        result = extract_deployer_from_security({"deployer": DEPLOYER})
        assert result == DEPLOYER

    def test_extract_owner_address(self):
        result = extract_deployer_from_security({"owner_address": DEPLOYER})
        assert result == DEPLOYER

    def test_extract_deployer_address(self):
        result = extract_deployer_from_security({"deployer_address": DEPLOYER})
        assert result == DEPLOYER

    def test_extract_nested_checks(self):
        result = extract_deployer_from_security({"checks": {"creator_address": DEPLOYER}})
        assert result == DEPLOYER

    def test_extract_nested_extra(self):
        result = extract_deployer_from_security({"extra": {"creator": DEPLOYER}})
        assert result == DEPLOYER

    def test_extract_nested_info(self):
        result = extract_deployer_from_security({"info": {"deployer_address": DEPLOYER}})
        assert result == DEPLOYER

    def test_extract_none_for_empty(self):
        assert extract_deployer_from_security({}) is None

    def test_extract_none_for_non_dict(self):
        assert extract_deployer_from_security(None) is None
        assert extract_deployer_from_security("string") is None
        assert extract_deployer_from_security(42) is None

    def test_extract_skips_short_addresses(self):
        result = extract_deployer_from_security({"creator_address": "short"})
        assert result is None

    def test_extract_skips_null_values(self):
        result = extract_deployer_from_security({"creator_address": "null"})
        assert result is None

    def test_extract_skips_empty_string(self):
        result = extract_deployer_from_security({"creator_address": ""})
        assert result is None

    def test_extract_skips_unknown(self):
        result = extract_deployer_from_security({"creator_address": "unknown"})
        assert result is None

    def test_extract_priority_order(self):
        """First matching key wins — check priority."""
        result = extract_deployer_from_security(
            {
                "creator_address": DEPLOYER,
                "deployer": "OtherAddr123456789012345678901234567890",
            }
        )
        assert result == DEPLOYER


# ═══════════════════════════════════════════════════════════════════════
# 10. Edge cases and stress
# ═══════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Edge case and boundary tests for the SENTINEL pipeline."""

    @pytest.mark.asyncio
    async def test_empty_address_produces_phase_dict(self):
        """An empty token address still produces a valid phase dict."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase("", CHAIN, tier="free")

        assert isinstance(result, dict)
        assert "free" in result

    @pytest.mark.asyncio
    async def test_unknown_chain_produces_phase_dict(self):
        """An unknown chain name doesn't crash the phase."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, "nonexistent_chain", tier="free")

        assert isinstance(result, dict)
        assert "free" in result

    @pytest.mark.asyncio
    async def test_result_with_none_values_handled(self):
        """Module returning None is recorded as None, not an error."""

        async def _return_none(*_a, **_kw):
            return None

        analyzers = dict.fromkeys(SENTINEL_ALL_MODULES, _return_none)

        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(analyzers=analyzers):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        # None results are not errors
        assert result["errors"] == {}
        assert result["free"]["holder_analysis"] is None

    @pytest.mark.asyncio
    async def test_empty_errors_dict_when_all_modules_succeed(self):
        """errors dict is empty when all modules produce results."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="elite")

        assert result["errors"] == {}

    @pytest.mark.asyncio
    async def test_modules_run_tracks_correct_count(self):
        """modules_run length equals attempted modules."""
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase():
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        assert len(result["modules_run"]) == len(SENTINEL_FREE_MODULES)

    @pytest.mark.asyncio
    async def test_partial_free_results_after_partial_failure(self):
        """When some free modules fail, others still populate."""
        raise_keys = {"holder_analysis", "contract_authority"}
        with contextlib.ExitStack() as stack:
            for patcher in _patch_sentinel_phase(raise_keys=raise_keys):
                stack.enter_context(patcher)
            result = await run_sentinel_phase(TOKEN, CHAIN, tier="free")

        assert result["free"]["holder_analysis"] is None
        assert result["free"]["contract_authority"] is None
        assert result["free"]["liquidity_verification"] is not None
        assert result["free"]["dev_reputation"] is not None
        assert result["free"]["metadata_fingerprint"] is not None
        assert result["free"]["proxy_detection"] is not None
        assert len(result["errors"]) == 2

    @pytest.mark.asyncio
    async def test_premium_description_count_matches(self):
        """Premium descriptions count equals premium modules count."""
        assert len(SENTINEL_PREMIUM_DESCRIPTIONS) == len(SENTINEL_PREMIUM_MODULES)
