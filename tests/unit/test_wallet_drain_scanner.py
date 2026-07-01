"""
Tests for Wallet Drain Scanner
=================================
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.wallet_drain_scanner import (
    WalletDrainScanner,
    _classify_drain_risk,
    _compute_drain_score,
    _generate_recommendation,
)


class TestDrainScore:
    """Test the core scoring algorithm."""

    def test_zero_score(self):
        """No signals should give near-zero score."""
        score = _compute_drain_score(
            unlimited_approvals=0,
            known_drainer_txs=0,
            suspicious_permit_count=0,
            max_approval_amount_score=0.0,
            nft_unlimited_approvals=0,
            delegate_call_count=0,
        )
        assert 0 <= score <= 5

    def test_critical_drain(self):
        """All drain signals maxed should give very high score."""
        score = _compute_drain_score(
            unlimited_approvals=4,
            known_drainer_txs=4,
            suspicious_permit_count=4,
            max_approval_amount_score=1.0,
            nft_unlimited_approvals=4,
            delegate_call_count=4,
        )
        assert score >= 75

    def test_moderate_drain(self):
        """Mixed signals should give moderate score."""
        score = _compute_drain_score(
            unlimited_approvals=1,
            known_drainer_txs=1,
            suspicious_permit_count=1,
            max_approval_amount_score=0.5,
            nft_unlimited_approvals=0,
            delegate_call_count=1,
        )
        assert 20 <= score <= 75

    def test_score_boundary(self):
        """Score should never exceed 100 or go below 0."""
        score = _compute_drain_score(
            unlimited_approvals=999,
            known_drainer_txs=999,
            suspicious_permit_count=999,
            max_approval_amount_score=999.0,
            nft_unlimited_approvals=999,
            delegate_call_count=999,
        )
        assert score <= 100.0
        assert score >= 0.0

    def test_unlimited_approval_weight(self):
        """More unlimited approvals should increase score."""
        low = _compute_drain_score(
            unlimited_approvals=0,
            known_drainer_txs=0,
            suspicious_permit_count=0,
            max_approval_amount_score=0.0,
            nft_unlimited_approvals=0,
            delegate_call_count=0,
        )
        high = _compute_drain_score(
            unlimited_approvals=3,
            known_drainer_txs=0,
            suspicious_permit_count=0,
            max_approval_amount_score=0.0,
            nft_unlimited_approvals=0,
            delegate_call_count=0,
        )
        assert high > low


class TestClassification:
    """Test drain risk classification thresholds."""

    def test_critical(self):
        assert _classify_drain_risk(80) == "critical"
        assert _classify_drain_risk(70) == "critical"

    def test_high(self):
        assert _classify_drain_risk(65) == "high"
        assert _classify_drain_risk(50) == "high"

    def test_moderate(self):
        assert _classify_drain_risk(45) == "moderate"
        assert _classify_drain_risk(30) == "moderate"

    def test_low(self):
        assert _classify_drain_risk(25) == "low"
        assert _classify_drain_risk(10) == "low"

    def test_none(self):
        assert _classify_drain_risk(5) == "none"
        assert _classify_drain_risk(0) == "none"


class TestRecommendation:
    """Test recommendation generation."""

    def test_critical_recommendation(self):
        rec = _generate_recommendation(85, 0.9)
        assert "CRITICAL" in rec
        assert "revoke" in rec.lower()

    def test_high_recommendation(self):
        rec = _generate_recommendation(60, 0.5)
        assert "HIGH" in rec

    def test_moderate_recommendation(self):
        rec = _generate_recommendation(40, 0.3)
        assert "MODERATE" in rec or "risky" in rec

    def test_low_with_dangerous_ratio(self):
        rec = _generate_recommendation(15, 0.1)
        assert "LOW" in rec or "Minor" in rec

    def test_none_recommendation(self):
        rec = _generate_recommendation(5, 0.0)
        assert "No drain" in rec or "healthy" in rec

    def test_different_severity_format(self):
        """Different severities should produce different messages."""
        low_rec = _generate_recommendation(5, 0.0)
        high_rec = _generate_recommendation(80, 0.9)
        assert low_rec != high_rec
        assert "CRITICAL" in high_rec


class TestWalletDrainScanner:
    """Test the WalletDrainScanner class."""

    def test_invalid_address_raises(self):
        """Invalid address should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid address"):
            WalletDrainScanner("nope", "ethereum")

    def test_valid_evm_address(self):
        """Valid EVM address should create scanner."""
        scanner = WalletDrainScanner(
            "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
            "ethereum",
        )
        assert scanner.address == "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"
        assert scanner.chain == "ethereum"
        assert scanner.is_evm

    def test_known_drainer_detection_bad_prefix(self):
        """Addresses with 0x0000 prefix should be detected as drainer."""
        assert WalletDrainScanner._is_known_drainer("0x000000000000000000000000000000000000dead")

    def test_known_drainer_benign_not_detected(self):
        """Well-known benign contracts should NOT be detected as drainers."""
        assert not WalletDrainScanner._is_known_drainer("0x7a250d5630b4cf539739df2c5dacb4c659f2488d")

    def test_known_drainer_dead_prefix(self):
        """Addresses with 0xdead prefix should be flagged."""
        assert WalletDrainScanner._is_known_drainer("0xdead000000000000000000000000000000000000")

    def test_validate_url_rejects_invalid(self):
        """Malformed URLs should be rejected."""
        from app.wallet_drain_scanner import _validate_url

        assert not _validate_url("javascript:alert(1)")
        assert _validate_url("https://api.etherscan.io/api")
