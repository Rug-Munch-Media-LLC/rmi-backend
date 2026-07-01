"""
Tests for Wash Trading Manipulation Detector
===============================================
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.wash_trading_detector import (
    _classify_wash_risk,
    _compute_wash_score,
    _generate_recommendation,
)


class TestWashScore:
    """Test the core scoring algorithm."""

    def test_zero_score(self):
        """No signals should give near-zero score."""
        score = _compute_wash_score(
            volume_tx_ratio=0.0,
            top_trader_concentration=0.0,
            buy_sell_correlation=0.0,
            small_trade_ratio=0.0,
            reapearring_address_count=0,
            liquidity_depth_ratio=0.0,
        )
        assert 0 <= score <= 15  # low end

    def test_critical_wash(self):
        """All wash signals maxed should give very high score."""
        score = _compute_wash_score(
            volume_tx_ratio=1.0,
            top_trader_concentration=0.9,
            buy_sell_correlation=1.0,
            small_trade_ratio=1.0,
            reapearring_address_count=10,
            liquidity_depth_ratio=1.0,
        )
        assert score >= 75

    def test_moderate_wash(self):
        """Mixed signals should give moderate score."""
        score = _compute_wash_score(
            volume_tx_ratio=0.5,
            top_trader_concentration=0.3,
            buy_sell_correlation=0.4,
            small_trade_ratio=0.5,
            reapearring_address_count=2,
            liquidity_depth_ratio=0.4,
        )
        assert 20 <= score <= 75

    def test_concentration_sensitivity(self):
        """Higher concentration should increase score."""
        low_conc = _compute_wash_score(
            volume_tx_ratio=0.3,
            top_trader_concentration=0.2,
            buy_sell_correlation=0.0,
            small_trade_ratio=0.0,
            reapearring_address_count=0,
            liquidity_depth_ratio=0.0,
        )
        high_conc = _compute_wash_score(
            volume_tx_ratio=0.3,
            top_trader_concentration=0.8,
            buy_sell_correlation=0.0,
            small_trade_ratio=0.0,
            reapearring_address_count=0,
            liquidity_depth_ratio=0.0,
        )
        assert high_conc > low_conc
        assert high_conc - low_conc == pytest.approx(15.0, abs=2.0)

    def test_correlation_sensitivity(self):
        """Buy/sell correlation should affect score."""
        low_corr = _compute_wash_score(
            volume_tx_ratio=0.3,
            top_trader_concentration=0.3,
            buy_sell_correlation=0.1,
            small_trade_ratio=0.0,
            reapearring_address_count=0,
            liquidity_depth_ratio=0.0,
        )
        high_corr = _compute_wash_score(
            volume_tx_ratio=0.3,
            top_trader_concentration=0.3,
            buy_sell_correlation=0.9,
            small_trade_ratio=0.0,
            reapearring_address_count=0,
            liquidity_depth_ratio=0.0,
        )
        assert high_corr > low_corr
        assert high_corr - low_corr == pytest.approx(16.0, abs=2.0)

    def test_score_boundary(self):
        """Score should never exceed 100 or go below 0."""
        score = _compute_wash_score(
            volume_tx_ratio=999.0,
            top_trader_concentration=9.0,
            buy_sell_correlation=999.0,
            small_trade_ratio=999.0,
            reapearring_address_count=999,
            liquidity_depth_ratio=999.0,
        )
        assert score <= 100.0
        assert score >= 0.0


class TestClassification:
    """Test wash risk classification thresholds."""

    def test_critical(self):
        assert _classify_wash_risk(80) == "critical"
        assert _classify_wash_risk(75) == "critical"

    def test_high(self):
        assert _classify_wash_risk(65) == "high"
        assert _classify_wash_risk(55) == "high"

    def test_moderate(self):
        assert _classify_wash_risk(45) == "moderate"
        assert _classify_wash_risk(35) == "moderate"

    def test_low(self):
        assert _classify_wash_risk(25) == "low"
        assert _classify_wash_risk(15) == "low"

    def test_none(self):
        assert _classify_wash_risk(10) == "none"
        assert _classify_wash_risk(0) == "none"


class TestRecommendation:
    """Test recommendation generation."""

    def test_critical_recommendation(self):
        rec = _generate_recommendation(80, 0.9)
        assert "CRITICAL" in rec
        assert "WASH TRADING" in rec

    def test_high_recommendation(self):
        rec = _generate_recommendation(60, 0.7)
        assert "HIGH" in rec

    def test_moderate_recommendation(self):
        rec = _generate_recommendation(40, 0.5)
        assert "MODERATE" in rec or "signals" in rec

    def test_none_recommendation(self):
        rec = _generate_recommendation(5, 0.3)
        assert "No wash" in rec or "organic" in rec

    def test_different_severity_format(self):
        """Different severities should produce different messages."""
        low_rec = _generate_recommendation(10, 0.3)
        high_rec = _generate_recommendation(80, 0.9)
        assert low_rec != high_rec
        assert "CRITICAL" in high_rec
