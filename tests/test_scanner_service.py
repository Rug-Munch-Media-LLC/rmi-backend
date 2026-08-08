"""Tests for scanner service scan_token() end-to-end with mocked modules.

Covers:
- scan_token() with FREE tier
- scan_token() with PRO tier
- scan_token() with ELITE tier
- scan_token() error handling
- _calculate_safety_score
- quick_scan_text
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ── Safety Score Calculation ──────────────────────────────────────────


def test_calculate_safety_score_baseline():
    """Safety score starts at 50 (neutral) with no modules."""
    from app.domains.scanner.service import _calculate_safety_score

    modules_run: list[dict] = [{"module": "test", "status": "error"}]
    score = _calculate_safety_score(modules_run)
    assert score == 50


def test_calculate_safety_score_clean_bonus():
    """Verified contract gives clean_bonus of +10."""
    from app.domains.scanner.service import _calculate_safety_score

    modules_run = [
        {
            "module": "verification",
            "status": "success",
            "result": {"result": "scanned", "is_verified": True},
        }
    ]
    score = _calculate_safety_score(modules_run)
    assert score == 60  # 50 + 10


def test_calculate_safety_score_honeypot_penalty():
    """Honeypot flag gives -35 penalty."""
    from app.domains.scanner.service import _calculate_safety_score

    modules_run = [
        {
            "module": "goplus",
            "status": "success",
            "result": {"result": "scanned", "is_honeypot": True},
        }
    ]
    score = _calculate_safety_score(modules_run)
    assert score == 15  # 50 - 35


def test_calculate_safety_score_spam_penalty():
    """Spam flag gives -30 penalty."""
    from app.domains.scanner.service import _calculate_safety_score

    modules_run = [
        {
            "module": "spam",
            "status": "success",
            "result": {"result": "scanned", "is_spam": True},
        }
    ]
    score = _calculate_safety_score(modules_run)
    assert score == 20  # 50 - 30


def test_calculate_safety_score_sanctioned():
    """Sanctioned flag gives -50 penalty."""
    from app.domains.scanner.service import _calculate_safety_score

    modules_run = [
        {
            "module": "spam",
            "status": "success",
            "result": {"result": "scanned", "is_sanctioned": True},
        }
    ]
    score = _calculate_safety_score(modules_run)
    assert score == 0  # 50 - 50


def test_calculate_safety_score_combined():
    """Multiple modules compound into final score."""
    from app.domains.scanner.service import _calculate_safety_score

    modules_run = [
        {
            "module": "goplus",
            "status": "success",
            "result": {
                "result": "scanned",
                "is_honeypot": False,
                "risk_score": 0,
            },
        },
        {
            "module": "verification",
            "status": "success",
            "result": {"result": "scanned", "is_verified": True},
        },
        {
            "module": "copycat",
            "status": "success",
            "result": {"result": "clean"},
        },
        {
            "module": "spam",
            "status": "success",
            "result": {"result": "scanned", "is_spam": False},
        },
    ]
    # Baseline 50 + clean_bonus: verified(+10) + copycat_clean(+5) = +15
    score = _calculate_safety_score(modules_run)
    # 50 + 15 = 65 (the +10 extra for 4+ modules was removed from the scoring function)
    assert score == 65


def test_calculate_safety_score_clamped():
    """Safety score is clamped to 0-100."""
    from app.domains.scanner.service import _calculate_safety_score

    modules_run = [
        {
            "module": "test",
            "status": "success",
            "result": {"result": "scanned", "is_sanctioned": True, "is_honeypot": True},
        }
    ]
    score = _calculate_safety_score(modules_run)
    assert score >= 0
    assert score <= 100


def test_calculate_safety_score_failed_critical_module_penalty():
    """A failed critical module (goplus/honeypot) must NOT inflate safety."""
    from app.domains.scanner.service.scoring import (
        DEFAULT_RISK_PENALTY,
        _calculate_safety_score,
    )

    # GoPlus returns an error result dict -> penalty applied, not skipped
    modules_run = [{"module": "goplus", "status": "success", "result": {"result": "error", "error": "HTTP 500"}}]
    score = _calculate_safety_score(modules_run)
    assert score == 50 - DEFAULT_RISK_PENALTY

    # Both critical vetting providers down -> token must not score safe
    all_down = [
        {"module": "goplus", "result": {"result": "unavailable"}},
        {"module": "honeypot", "result": {"result": "error", "error": "Timeout"}},
    ]
    score = _calculate_safety_score(all_down)
    assert score == 50 - 2 * DEFAULT_RISK_PENALTY


def test_calculate_safety_score_failed_non_critical_module_skip():
    """Non-critical modules in error state keep the skip behavior."""
    from app.domains.scanner.service.scoring import _calculate_safety_score

    modules_run = [
        {"module": "copycat", "result": {"result": "error", "error": "API down"}},
        {"module": "spam", "result": {"result": "pending"}},
    ]
    score = _calculate_safety_score(modules_run)
    assert score == 50


def test_risk_level_from_safety_medium_not_safe():
    """Scores 40-59 are medium risk, not safe; >= 60 is safe (None)."""
    from app.domains.scanner.service.scoring import _risk_level_from_safety

    assert _risk_level_from_safety(100) is None  # safe
    assert _risk_level_from_safety(60) is None  # safe/low boundary
    assert _risk_level_from_safety(59) == "medium"
    assert _risk_level_from_safety(40) == "medium"
    assert _risk_level_from_safety(39) == "high"
    assert _risk_level_from_safety(20) == "high"
    assert _risk_level_from_safety(19) == "critical"


# ── ScanResult Model ──────────────────────────────────────────────────


def test_scan_result_defaults():
    """ScanResult has correct default values."""
    from app.domains.scanner.models import ScanResult

    result = ScanResult(token_address="0xtoken", chain="ethereum")

    assert result.token_address == "0xtoken"
    assert result.chain == "ethereum"
    assert result.safety_score == 50
    assert result.symbol == ""
    assert result.risk_flags == []
    assert result.confidence == 0


# ── scan_token() FREE tier ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_token_free_tier():
    """scan_token with FREE tier runs core modules."""
    with (
        patch("app.domains.scanner.service.scan.fetch_market_data") as mock_md,
        patch("app.domains.scanner.service.scan._check_honeypot_is") as mock_hp,
        patch("app.domains.scanner.service.scan._check_goplus_security") as mock_gp,
        patch("app.domains.scanner.service.scan._check_copycat") as mock_cc,
        patch("app.domains.scanner.service.scan._check_spam") as mock_sp,
        patch("app.domains.scanner.service.scan._check_blockscout") as mock_bs,
        patch("app.domains.scanner.service.scan._check_defi_scanner") as mock_ds,
    ):
        mock_md.return_value = {"price": 1.0, "volume_24h": 50000}
        mock_hp.return_value = {"check": "honeypot", "result": "scanned", "is_honeypot": False}
        mock_gp.return_value = {
            "check": "goplus",
            "result": "scanned",
            "is_honeypot": False,
            "risk_score": 0,
        }
        mock_cc.return_value = {"check": "copycat", "result": "clean"}
        mock_sp.return_value = {"check": "spam", "result": "scanned", "is_spam": False}
        mock_bs.return_value = {"check": "blockscout", "result": "pending"}
        mock_ds.return_value = {"check": "defi_scanner", "result": "pending"}

        from app.domains.scanner.service import scan_token

        result = await scan_token("0xtoken", "ethereum", tiers=["free"])

        assert result.token_address == "0xtoken"
        assert result.chain == "ethereum"
        assert result.safety_score >= 30
        assert len(result.modules_run) >= 5


@pytest.mark.asyncio
async def test_scan_token_free_tier_honeypot():
    """scan_token FREE tier flags a honeypot correctly."""
    with (
        patch("app.domains.scanner.service.scan.fetch_market_data") as mock_md,
        patch("app.domains.scanner.service.scan._check_honeypot_is") as mock_hp,
        patch("app.domains.scanner.service.scan._check_goplus_security") as mock_gp,
        patch("app.domains.scanner.service.scan._check_copycat") as mock_cc,
        patch("app.domains.scanner.service.scan._check_spam") as mock_sp,
        patch("app.domains.scanner.service.scan._check_blockscout") as mock_bs,
        patch("app.domains.scanner.service.scan._check_defi_scanner") as mock_ds,
    ):
        mock_md.return_value = {"price": 0.001, "volume_24h": 100}
        mock_hp.return_value = {
            "check": "honeypot",
            "result": "scanned",
            "is_honeypot": True,
            "risk_score": 85,
        }
        mock_gp.return_value = {
            "check": "goplus",
            "result": "scanned",
            "is_honeypot": True,
            "risk_score": 80,
            "risk_flags": {"honeypot"},
        }
        mock_cc.return_value = {"check": "copycat", "result": "clean"}
        mock_sp.return_value = {"check": "spam", "result": "scanned", "is_spam": False}
        mock_bs.return_value = {"check": "blockscout", "result": "pending"}
        mock_ds.return_value = {"check": "defi_scanner", "result": "pending"}

        from app.domains.scanner.service import scan_token

        result = await scan_token("0xhoneypot", "ethereum", tiers=["free"])

        assert result.safety_score < 40
        assert "honeypot" in result.risk_flags or "high_risk_score" in result.risk_flags


# ── scan_token() PRO tier ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_token_pro_tier():
    """scan_token with PRO tier runs more modules."""
    with (
        patch("app.domains.scanner.service.scan.fetch_market_data") as mock_md,
        patch("app.domains.scanner.service.scan._check_honeypot_is") as mock_hp,
        patch("app.domains.scanner.service.scan._check_goplus_security") as mock_gp,
        patch("app.domains.scanner.service.scan._check_copycat") as mock_cc,
        patch("app.domains.scanner.service.scan._check_spam") as mock_sp,
        patch("app.domains.scanner.service.scan._check_blockscout") as mock_bs,
        patch("app.domains.scanner.service.scan._check_defi_scanner") as mock_ds,
        patch("app.domains.scanner.service.scan._get_deployer_info") as mock_di,
        patch("app.domains.scanner.service.scan._get_holder_data") as mock_hd,
        patch("app.domains.scanner.service.scan._check_contract_verification") as mock_cv,
        patch("app.domains.scanner.service.scan._check_liquidity_lock_multi") as mock_ll,
        patch("app.domains.scanner.service.scan._check_bundle") as mock_bd,
    ):
        for m in [
            mock_md,
            mock_hp,
            mock_gp,
            mock_cc,
            mock_sp,
            mock_bs,
            mock_ds,
            mock_di,
            mock_hd,
            mock_cv,
            mock_ll,
            mock_bd,
        ]:
            m.return_value = {"result": "scanned"}

        mock_cc.return_value = {"check": "copycat", "result": "clean"}
        mock_cv.return_value = {"check": "verified", "result": "scanned", "is_verified": True}

        from app.domains.scanner.service import scan_token

        result = await scan_token("0xtoken", "ethereum", tiers=["pro"])

        assert result.token_address == "0xtoken"
        assert len(result.modules_run) >= 10  # free(7) + pro(5) = 12


# ── scan_token() error handling ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_scan_token_module_failure():
    """scan_token handles a module failure gracefully."""
    with (
        patch("app.domains.scanner.service.scan.fetch_market_data") as mock_md,
        patch("app.domains.scanner.service.scan._check_honeypot_is") as mock_hp,
        patch("app.domains.scanner.service.scan._check_goplus_security") as mock_gp,
        patch("app.domains.scanner.service.scan._check_copycat") as mock_cc,
        patch("app.domains.scanner.service.scan._check_spam") as mock_sp,
        patch("app.domains.scanner.service.scan._check_blockscout") as mock_bs,
        patch("app.domains.scanner.service.scan._check_defi_scanner") as mock_ds,
    ):
        mock_md.side_effect = RuntimeError("API unavailable")
        mock_hp.side_effect = RuntimeError("Service down")
        mock_gp.return_value = {"check": "goplus", "result": "scanned"}
        mock_cc.return_value = {"check": "copycat", "result": "clean"}
        mock_sp.return_value = {"check": "spam", "result": "scanned"}
        mock_bs.return_value = {"check": "blockscout", "result": "pending"}
        mock_ds.return_value = {"check": "defi_scanner", "result": "pending"}

        from app.domains.scanner.service import scan_token

        result = await scan_token("0xtoken", "ethereum", tiers=["free"])

        assert result is not None
        assert result.safety_score >= 0
        assert result.safety_score <= 100
        # Some modules should have "error" status
        error_modules = [m for m in result.modules_run if m.get("status") == "error"]
        assert len(error_modules) >= 1


# ── Chain ID mappings ──────────────────────────────────────────────────


def test_goplus_chain_ids():
    """GoPlus chain ID mapping covers major chains."""
    from app.domains.scanner.modules import GOPLUS_CHAIN_IDS

    assert GOPLUS_CHAIN_IDS["ethereum"] == "1"
    assert GOPLUS_CHAIN_IDS["solana"] == "solana"
    assert GOPLUS_CHAIN_IDS["bsc"] == "56"
    assert GOPLUS_CHAIN_IDS["base"] == "8453"
    assert GOPLUS_CHAIN_IDS["arbitrum"] == "42161"
    assert GOPLUS_CHAIN_IDS["polygon"] == "137"


def test_explorer_apis():
    """Explorer API mapping covers major EVM chains."""
    from app.domains.scanner.modules import EXPLORER_APIS

    assert "ethereum" in EXPLORER_APIS
    assert "bsc" in EXPLORER_APIS
    assert "arbitrum" in EXPLORER_APIS
    assert "base" in EXPLORER_APIS
