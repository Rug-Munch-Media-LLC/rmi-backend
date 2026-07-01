"""
Tests for Profile Flip / Identity Change Detector.
"""

import pytest

from app.profile_flip_detector import (
    _check_cross_project_laundering,
    _compute_identity_flip_score,
    _detect_branding_flip,
    _detect_wallet_behavior_flip,
    _domain_age_risk,
    _extract_domain,
    detect_profile_flip,
)


class TestDomainRisk:
    def test_suspicious_tld(self):
        result = _domain_age_risk("project.xyz")
        assert result["risk"] >= 0.3
        assert any("suspicious_tld" in f for f in result["flags"])

    def test_auto_generated_domain(self):
        result = _domain_age_risk("abcdefghijklmnopqrstuvwxyz.com")
        assert result["risk"] > 0.1

    def test_clean_domain(self):
        result = _domain_age_risk("rugmunch.io")
        assert result["risk"] == 0.0
        assert result["flags"] == []


class TestBrandingFlip:
    def test_rebrand_keyword(self):
        result = _detect_branding_flip("SafeMoon", "we are rebranding to V2")
        assert result["risk"] > 0.1
        assert any("rebrand_keyword" in f for f in result["flags"])

    def test_generic_description(self):
        result = _detect_branding_flip("TokenX", "the next 100x moon shot")
        assert result["risk"] >= 0.1

    def test_consistent_branding(self):
        result = _detect_branding_flip("RugMunch", "RugMunch is a crypto security scanner")
        assert result["risk"] == 0.0


class TestWalletBehavior:
    def test_holder_to_drainer_high_risk(self):
        result = _detect_wallet_behavior_flip(
            {
                "previous_behavior": "holder",
                "current_behavior": "drainer",
                "days_active": 365,
                "tx_count": 100,
                "eth_balance_change": -50,
            }
        )
        assert result["risk"] >= 0.9
        assert any("behavior_transition" in f for f in result["flags"])

    def test_dormant_wallet_reactivated(self):
        result = _detect_wallet_behavior_flip(
            {
                "previous_behavior": "holder",
                "current_behavior": "trader",
                "days_active": 200,
                "tx_count": 20,
                "eth_balance_change": 0,
            }
        )
        assert any("dormant_wallet_reactivated" in f for f in result["flags"])

    def test_no_data(self):
        result = _detect_wallet_behavior_flip(None)
        assert result["risk"] == 0.0


class TestIdentityFlipScore:
    def test_critical_flip(self):
        result = _compute_identity_flip_score(
            social_risk=0.9,
            domain_risk=0.8,
            branding_risk=0.7,
            wallet_behavior_risk=0.9,
            cross_project_signals=["previous_scam_project:TestScam"],
            age_days=5,
        )
        assert result["score"] >= 70
        assert result["classification"] == "critical"

    def test_low_risk(self):
        result = _compute_identity_flip_score(
            social_risk=0.0,
            domain_risk=0.0,
            branding_risk=0.0,
            wallet_behavior_risk=0.0,
            cross_project_signals=[],
            age_days=365,
        )
        assert result["score"] < 25
        assert result["classification"] == "low"

    def test_young_project_bonus(self):
        young = _compute_identity_flip_score(0.5, 0, 0, 0, [], 5)
        old = _compute_identity_flip_score(0.5, 0, 0, 0, [], 365)
        assert young["score"] > old["score"]


class TestCrossProjectLaundering:
    def test_previous_scam(self):
        signals = _check_cross_project_laundering(
            "0xabc",
            [
                {"name": "SafeProject", "deployer": "0xabc", "status": "active"},
                {"name": "RugProject", "deployer": "0xabc", "status": "rug_pull"},
            ],
        )
        assert len(signals) >= 1
        assert any("previous_scam_project" in s for s in signals)

    def test_no_laundering(self):
        signals = _check_cross_project_laundering(None, [])
        assert signals == []


class TestExtractDomain:
    def test_full_url(self):
        assert _extract_domain("https://rugmunch.io/page") == "rugmunch.io"

    def test_no_domain(self):
        assert _extract_domain("not-a-url") is None


class TestDetectProfileFlip:
    @pytest.mark.asyncio
    async def test_with_project_name(self):
        result = await detect_profile_flip(
            project_name="TestProject",
            project_url="https://testproject.xyz",
            social_handles={"twitter": "test12345"},
        )
        assert "score" in result
        assert "classification" in result
        assert "signals" in result
        assert "breakdown" in result
        assert isinstance(result["score"], float)
