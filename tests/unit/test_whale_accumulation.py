"""
Tests for Whale Accumulation Pattern Detector
===============================================
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.whale_accumulation import (
    _classify_accumulation,
    _compute_accumulation_score,
    _generate_recommendation,
)


class TestAccumulationScore:
    """Test the core scoring algorithm."""

    def test_zero_score(self):
        """No signals should give near-zero score."""
        score = _compute_accumulation_score(
            buy_volume_ratio=1.0,
            holder_concentration=0.0,
            tx_frequency=0.0,
            wallet_age_days=365,
            is_smart_money=False,
            recent_large_buys=0,
        )
        assert 0 <= score <= 20  # low end

    def test_heavy_accumulation(self):
        """All signals maxed should give high score."""
        score = _compute_accumulation_score(
            buy_volume_ratio=3.0,
            holder_concentration=0.8,
            tx_frequency=100.0,
            wallet_age_days=1,
            is_smart_money=True,
            recent_large_buys=10,
        )
        assert score >= 70

    def test_moderate_accumulation(self):
        """Mixed signals should give moderate score."""
        score = _compute_accumulation_score(
            buy_volume_ratio=1.8,
            holder_concentration=0.3,
            tx_frequency=15.0,
            wallet_age_days=30,
            is_smart_money=False,
            recent_large_buys=2,
        )
        assert 20 <= score <= 70

    def test_smart_money_bonus(self):
        """Smart money involvement should boost score."""
        without_sm = _compute_accumulation_score(
            buy_volume_ratio=1.0,
            holder_concentration=0.3,
            tx_frequency=5.0,
            wallet_age_days=100,
            is_smart_money=False,
            recent_large_buys=0,
        )
        with_sm = _compute_accumulation_score(
            buy_volume_ratio=1.0,
            holder_concentration=0.3,
            tx_frequency=5.0,
            wallet_age_days=100,
            is_smart_money=True,
            recent_large_buys=0,
        )
        assert with_sm > without_sm
        assert with_sm - without_sm == pytest.approx(20.0, abs=1.0)

    def test_new_wallet_penalty(self):
        """Newer wallets should score higher (more suspicious)."""
        old = _compute_accumulation_score(
            buy_volume_ratio=1.0,
            holder_concentration=0.3,
            tx_frequency=10.0,
            wallet_age_days=365,
            is_smart_money=False,
            recent_large_buys=0,
        )
        new = _compute_accumulation_score(
            buy_volume_ratio=1.0,
            holder_concentration=0.3,
            tx_frequency=10.0,
            wallet_age_days=1,
            is_smart_money=False,
            recent_large_buys=0,
        )
        assert new > old

    def test_score_boundary(self):
        """Score should never exceed 100."""
        score = _compute_accumulation_score(
            buy_volume_ratio=999.0,
            holder_concentration=1.0,
            tx_frequency=999.0,
            wallet_age_days=0,
            is_smart_money=True,
            recent_large_buys=999,
        )
        assert score <= 100.0
        assert score >= 0.0


class TestClassification:
    """Test accumulation classification thresholds."""

    def test_critical(self):
        assert _classify_accumulation(85) == "critical"
        assert _classify_accumulation(80) == "critical"

    def test_high(self):
        assert _classify_accumulation(70) == "high"
        assert _classify_accumulation(60) == "high"

    def test_moderate(self):
        assert _classify_accumulation(50) == "moderate"
        assert _classify_accumulation(40) == "moderate"

    def test_low(self):
        assert _classify_accumulation(30) == "low"
        assert _classify_accumulation(20) == "low"

    def test_none(self):
        assert _classify_accumulation(10) == "none"
        assert _classify_accumulation(0) == "none"


class TestRecommendation:
    """Test recommendation generation."""

    def test_critical_recommendation(self):
        rec = _generate_recommendation(85, "trader")
        assert "CRITICAL" in rec
        assert "ACCUMULATION" in rec

    def test_high_recommendation(self):
        rec = _generate_recommendation(65, "trader")
        assert "HIGH" in rec

    def test_none_recommendation(self):
        rec = _generate_recommendation(5, "trader")
        assert "No accumulation" in rec

    def test_different_personas(self):
        """Different personas should get different messaging."""
        trader_rec = _generate_recommendation(85, "trader")
        generic_rec = _generate_recommendation(85, "investor")
        assert trader_rec != generic_rec
        assert "position" in trader_rec.lower()
