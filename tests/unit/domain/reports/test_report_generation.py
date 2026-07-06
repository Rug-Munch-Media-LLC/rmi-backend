"""Tests for app/domain/reports/generator.py - report generation tests."""

from app.domains.reports.generator import (
    _compute_risk_token,
    _compute_risk_wallet,
)


class TestRiskComputation:
    """Tests for risk score computation."""

    def test_compute_risk_token_low_risk(self):
        """Test risk score for low-risk token."""
        token_data = {
            "token": type(
                "Token",
                (),
                {
                    "is_honeypot": False,
                    "is_mintable": False,
                    "is_proxy": False,
                    "tax_buy_bps": 100,
                    "tax_sell_bps": 100,
                    "risk_factors": [],
                },
            )()
        }
        score, _factors, tier = _compute_risk_token(token_data)
        assert score < 25
        assert tier.name == "LOW"

    def test_compute_risk_token_high_risk(self):
        """Test risk score for token with multiple critical flags.

        A token with honeypot + mintable + proxy + 20% buy/sell taxes
        is CRITICAL, not HIGH. Per generator.py risk logic:
          honeypot(50) + mintable(20) + proxy(10) +
          buy_tax>10%(15) + sell_tax>10%(15) +
          2 risk_factors(10 capped) = 120, capped at 100
        Tier thresholds: <25 LOW, <50 MEDIUM, <75 HIGH, >=75 CRITICAL.
        A score of 100 with all those flags = CRITICAL.
        """
        token_data = {
            "token": type(
                "Token",
                (),
                {
                    "is_honeypot": True,
                    "is_mintable": True,
                    "is_proxy": True,
                    "tax_buy_bps": 2000,
                    "tax_sell_bps": 2000,
                    "risk_factors": ["test1", "test2"],
                },
            )()
        }
        score, _factors, tier = _compute_risk_token(token_data)
        assert score >= 75  # Multiple critical flags → high score
        assert tier.name == "CRITICAL"  # Score 100 = CRITICAL not HIGH

    def test_compute_risk_token_max_risk(self):
        """Test risk score is capped at 100."""
        token_data = {
            "token": type(
                "Token",
                (),
                {
                    "is_honeypot": True,
                    "is_mintable": True,
                    "is_proxy": True,
                    "tax_buy_bps": 5000,
                    "tax_sell_bps": 5000,
                    "risk_factors": ["a", "b", "c", "d", "e"],
                },
            )()
        }
        score, _factors, _tier = _compute_risk_token(token_data)
        assert score == 100  # Should be capped

    def test_compute_risk_wallet_low_risk(self):
        """Test risk score for low-risk wallet."""
        wallet_data = {
            "wallet": type(
                "Wallet",
                (),
                {
                    "is_suspicious": False,
                    "tx_count": 100,
                },
            )(),
            "entity": {},
            "news": [],
        }
        score, _factors, tier = _compute_risk_wallet(wallet_data)
        assert score < 25
        assert tier.name == "LOW"

    def test_compute_risk_wallet_high_risk(self):
        """Test risk score for high-risk wallet."""
        wallet_data = {
            "wallet": type(
                "Wallet",
                (),
                {
                    "is_suspicious": True,
                    "tx_count": 15000,
                },
            )(),
            "entity": {"wallets": ["a", "b", "c", "d", "e"]},
            "news": [],
        }
        score, _factors, tier = _compute_risk_wallet(wallet_data)
        assert score >= 50
        assert tier.name in ["MEDIUM", "HIGH", "CRITICAL"]


class TestTemplateFallback:
    """Tests for _template_fallback function."""

    def test_template_fallback_executive_summary(self):
        """Test executive summary template."""
        from app.domains.reports.generator import _template_fallback

        ctx = {
            "subject_id": "eth:0x123",
            "risk_score": 75,
            "risk_tier": "HIGH",
            "risk_factors": "test_risk",
        }
        result = _template_fallback("executive_summary", ctx)
        assert "Executive Summary" in result
        assert "75" in result
        assert "HIGH" in result

    def test_template_fallback_recommendation(self):
        """Test recommendation template."""
        from app.domains.reports.generator import _template_fallback

        ctx = {
            "subject_id": "eth:0x123",
            "risk_score": 75,
            "risk_tier": "HIGH",
            "risk_factors": "test_risk",
        }
        result = _template_fallback("recommendation", ctx)
        assert "AVOID" in result  # Risk >= 75 should be AVOID
        assert "75" in result
