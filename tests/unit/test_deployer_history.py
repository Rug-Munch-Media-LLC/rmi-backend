"""
Tests for DeployerHistoryAnalyzer
"""

import unittest

import pytest

from app.deployer_history import (
    DeployerHistoryAnalyzer,
    DeployerProfile,
    _classify_deployer_risk,
    _compute_deployer_risk,
    _detect_chain_from_address,
    _generate_recommendation,
)


class TestDeployerHistoryScoring(unittest.TestCase):
    """Test the core scoring and classification engine."""

    def test_safe_deployer(self) -> None:
        """A deployer with no rugs should score low."""
        profile = DeployerProfile(address="0x1234567890abcdef1234567890abcdef12345678")
        profile.total_tokens_deployed = 5
        profile.active_tokens = 5
        profile.rug_tokens = 0
        profile.honeypot_tokens = 0
        profile.dead_tokens = 0
        profile.avg_token_lifespan_days = 365.0

        score = _compute_deployer_risk(profile)
        assert score < 20
        assert _classify_deployer_risk(score) == "safe"

    def test_serial_scammer(self) -> None:
        """A deployer with multiple rugs should score high."""
        profile = DeployerProfile(address="0xabcdef1234567890abcdef1234567890abcdef12")
        profile.total_tokens_deployed = 10
        profile.active_tokens = 1
        profile.rug_tokens = 7
        profile.honeypot_tokens = 2
        profile.dead_tokens = 9
        profile.avg_token_lifespan_days = 3.0
        profile.patterns_detected = [
            "serial_scammer:multiple_rugs",
            "serial_scammer:short_lived_tokens",
        ]

        score = _compute_deployer_risk(profile)
        assert score > 40
        assert _classify_deployer_risk(score) in ("critical", "high")

    def test_moderate_risk(self) -> None:
        """A deployer with some concerns."""
        profile = DeployerProfile(address="0xdeadbeef1234567890abcdef1234567890deadbeef")
        profile.total_tokens_deployed = 5
        profile.active_tokens = 3
        profile.rug_tokens = 1
        profile.honeypot_tokens = 0
        profile.dead_tokens = 2
        profile.avg_token_lifespan_days = 45.0

        score = _compute_deployer_risk(profile)
        assert score >= 10
        assert score < 60

    def test_recommendation_safe(self) -> None:
        rec = _generate_recommendation(
            DeployerProfile(
                address="0xaaaabbbbccccddddeeeeffff0000111122223333",
                total_tokens_deployed=3,
                active_tokens=3,
                avg_token_lifespan_days=200.0,
            ),
            5.0,
        )
        assert "SAFE" in rec.upper()

    def test_recommendation_critical(self) -> None:
        profile = DeployerProfile(
            address="0xbbbccccddddeeeeffff0000111122223333aaaa",
            total_tokens_deployed=10,
            rug_tokens=7,
            honeypot_tokens=2,
            patterns_detected=["serial_scammer:multiple_rugs"],
        )
        rec = _generate_recommendation(profile, 85.0)
        assert "CRITICAL" in rec.upper()

    def test_classify_thresholds(self) -> None:
        assert _classify_deployer_risk(75) == "critical"
        assert _classify_deployer_risk(55) == "high"
        assert _classify_deployer_risk(35) == "moderate"
        assert _classify_deployer_risk(15) == "low"
        assert _classify_deployer_risk(5) == "safe"

    def test_address_type_detection(self) -> None:
        evm = _detect_chain_from_address("0x1234567890abcdef1234567890abcdef12345678")
        assert "ethereum" in evm
        assert "bsc" in evm

        sol = _detect_chain_from_address("DeFi123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefg")
        assert "solana" in sol

    def test_invalid_address(self) -> None:
        with pytest.raises(ValueError):
            DeployerHistoryAnalyzer("not_an_address")
        with pytest.raises(ValueError):
            DeployerHistoryAnalyzer("")


class TestDeployerHistoryIntegration(unittest.TestCase):
    """Integration-level tests (no external calls)."""

    def test_empty_analysis_handles_no_tokens(self) -> None:
        """The analyzer should handle the case where no tokens are found."""
        import asyncio

        from app.deployer_history import DeployerHistoryAnalyzer

        async def run() -> dict[str, object]:
            analyzer = DeployerHistoryAnalyzer("0x0000000000000000000000000000000000000001")
            result = await analyzer.analyze()
            assert "errors" in result
            assert "risk_level" in result
            # Should not crash
            return result

        result = asyncio.run(run())
        assert result["total_tokens_deployed"] == 0

    def test_scoring_empty_profile(self) -> None:
        """Even empty profiles should not crash scoring."""
        profile = DeployerProfile(address="0x1111111111111111111111111111111111111111")
        score = _compute_deployer_risk(profile)
        assert isinstance(score, float)
        assert score >= 0


if __name__ == "__main__":
    unittest.main()
