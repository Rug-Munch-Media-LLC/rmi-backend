"""Tests for own_bundle_detect — ClickHouse-powered bundle buy detection.

Tests the scoring functions and result structure without requiring
ClickHouse. The scoring functions are pure logic that can be tested
deterministically.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.domains.scanner.engines import own_bundle_detect as bd


# ── Sample test data ─────────────────────────────────────────────────────

SINGLE_BLOCK = [
    {"block_number": 100, "buyer_count": 5, "total_buys": 5},
]

MULTI_HIGH_BLOCKS = [
    {"block_number": 100, "buyer_count": 20, "total_buys": 25},
    {"block_number": 101, "buyer_count": 10, "total_buys": 12},
]

ONE_FUNDER_TWO_WALLETS: list[dict[str, Any]] = [
    {"funder": "0xfunder1", "funded_count": 2, "funded_addresses_sample": ["0xa", "0xb"]},
]

SEVERAL_FUNDERS: list[dict[str, Any]] = [
    {"funder": "0xfunder1", "funded_count": 5, "funded_addresses_sample": ["0xa", "0xb", "0xc", "0xd", "0xe"]},
    {"funder": "0xfunder2", "funded_count": 3, "funded_addresses_sample": ["0xf", "0xg", "0xh"]},
    {"funder": "0xfunder3", "funded_count": 3, "funded_addresses_sample": ["0xi", "0xj", "0xk"]},
]

HIGH_CONCENTRATION_DATA = {
    "top3_pct": 95.0,
    "top10_pct": 99.0,
    "holder_count": 50,
}

MODERATE_CONCENTRATION_DATA = {
    "top3_pct": 45.0,
    "top10_pct": 75.0,
    "holder_count": 100,
}

LOW_CONCENTRATION_DATA = {
    "top3_pct": 10.0,
    "top10_pct": 25.0,
    "holder_count": 500,
}


# ── Atomic Block Scoring ─────────────────────────────────────────────────


class TestScoreAtomicBlocks:
    """Tests for _score_atomic_blocks."""

    def test_no_blocks_does_nothing(self):
        """Empty blocks list should not modify result."""
        result: dict[str, Any] = {}
        bd._score_atomic_blocks(result, [])
        assert result == {}

    def test_single_block_5_buyers_scores_40(self):
        """5 buyers in a block should score 40."""
        result: dict[str, Any] = {}
        bd._score_atomic_blocks(result, SINGLE_BLOCK)
        assert result["atomic_block_score"] == 40

    def test_single_block_3_buyers_scores_20(self):
        """3 buyers in a block should score 20."""
        result: dict[str, Any] = {}
        bd._score_atomic_blocks(
            result,
            [
                {"block_number": 100, "buyer_count": 3, "total_buys": 3},
            ],
        )
        assert result["atomic_block_score"] == 20

    def test_20_buyers_scores_100(self):
        """20+ buyers in a block should score 100."""
        result: dict[str, Any] = {}
        bd._score_atomic_blocks(result, MULTI_HIGH_BLOCKS)
        assert result["atomic_block_score"] == 100

    def test_7_buyers_scores_60_with_bonus(self):
        """Multiple high-buyer blocks should get a bonus."""
        result: dict[str, Any] = {}
        blocks = [
            {"block_number": 100, "buyer_count": 7, "total_buys": 7},
            {"block_number": 101, "buyer_count": 5, "total_buys": 6},
            {"block_number": 102, "buyer_count": 5, "total_buys": 5},
        ]
        bd._score_atomic_blocks(result, blocks)
        # Base 60 + 15 bonus for 3 blocks with >=5 buyers = 75
        assert result["atomic_block_score"] == 75

    def test_sets_bundle_blocks(self):
        """Should set bundle_blocks on result."""
        result: dict[str, Any] = {}
        bd._score_atomic_blocks(result, SINGLE_BLOCK)
        assert result["bundle_blocks"] == SINGLE_BLOCK


# ── Common Funder Scoring ────────────────────────────────────────────────


class TestScoreCommonFunders:
    """Tests for _score_common_funders."""

    def test_no_funders_does_nothing(self):
        """Empty funders list should not modify result."""
        result: dict[str, Any] = {}
        bd._score_common_funders(result, [])
        assert result == {}

    def test_one_funder_2_wallets_scores_20(self):
        """A funder that funded 2 wallets should score 20."""
        result: dict[str, Any] = {}
        bd._score_common_funders(result, ONE_FUNDER_TWO_WALLETS)
        assert result["common_funder_score"] == 20

    def test_funder_with_5_wallets_scores_70(self):
        """A funder that funded 5 wallets should score 70 (+ bonus for 3 multi-funders)."""
        result: dict[str, Any] = {}
        bd._score_common_funders(result, SEVERAL_FUNDERS)
        # Base 70 + 10 bonus for 3 funders with >=3 funded = 80
        assert result["common_funder_score"] == 80

    def test_multiple_multi_funders_adds_bonus(self):
        """3+ funders each funding 3+ wallets add a bonus."""
        result: dict[str, Any] = {}
        bd._score_common_funders(result, SEVERAL_FUNDERS)
        # Base 70 + 10 bonus for 3 funders with >=3 funded
        assert result["common_funder_score"] == 80

    def test_sets_common_funders_list(self):
        """Should set common_funders on result."""
        result: dict[str, Any] = {}
        bd._score_common_funders(result, ONE_FUNDER_TWO_WALLETS)
        assert result["common_funders"] == ONE_FUNDER_TWO_WALLETS


# ── Holder Concentration Scoring ─────────────────────────────────────────


class TestScoreHolderConcentration:
    """Tests for _score_holder_concentration."""

    def test_high_concentration_scores_100(self):
        """Top 3 holding >90% should score 100."""
        result: dict[str, Any] = {}
        bd._score_holder_concentration(result, HIGH_CONCENTRATION_DATA)
        assert result["distribution_anomaly_score"] == 100
        assert result["concentration_score"] == 100

    def test_moderate_concentration_scores_30_50(self):
        """Top 3 holding 45% with top 10 holding 75% should score moderate."""
        result: dict[str, Any] = {}
        bd._score_holder_concentration(result, MODERATE_CONCENTRATION_DATA)
        assert result["distribution_anomaly_score"] == 30
        assert result["concentration_score"] == 30

    def test_low_concentration_scores_zero(self):
        """Broad distribution should not set score keys."""
        result: dict[str, Any] = {}
        bd._score_holder_concentration(result, LOW_CONCENTRATION_DATA)
        assert result.get("distribution_anomaly_score", 0) == 0
        assert result.get("concentration_score", 0) == 0

    def test_sets_holder_data(self):
        """Should set top3_pct, top10_pct, holder_count."""
        result: dict[str, Any] = {}
        bd._score_holder_concentration(result, HIGH_CONCENTRATION_DATA)
        assert result["top3_holder_pct"] == 95.0
        assert result["top10_holder_pct"] == 99.0
        assert result["holder_count"] == 50


# ── Aggregate Scoring ────────────────────────────────────────────────────


class TestComputeBundleScore:
    """Tests for _compute_bundle_score — aggregate scoring."""

    def test_high_scores_equal_bundled_critical(self):
        """Scores >=70 should result in is_bundled=True, risk_label='critical'."""
        result: dict[str, Any] = {
            "atomic_block_score": 100,
            "common_funder_score": 70,
            "distribution_anomaly_score": 80,
            "concentration_score": 60,
        }
        bd._compute_bundle_score(result)
        assert result["is_bundled"] is True
        assert result["risk_label"] == "critical"

    def test_medium_scores_equal_bundled_high(self):
        """Scores 50-69 should result in is_bundled=True, risk_label='high'."""
        result: dict[str, Any] = {
            "atomic_block_score": 60,
            "common_funder_score": 40,
            "distribution_anomaly_score": 60,
            "concentration_score": 40,
        }
        bd._compute_bundle_score(result)
        assert result["is_bundled"] is True
        assert result["risk_label"] == "high"

    def test_low_scores_equal_not_bundled(self):
        """Scores <30 should not be flagged as bundled."""
        result: dict[str, Any] = {
            "atomic_block_score": 0,
            "common_funder_score": 0,
            "distribution_anomaly_score": 20,
            "concentration_score": 0,
            "is_bundled": False,  # Set as default before scoring
        }
        bd._compute_bundle_score(result)
        assert result.get("is_bundled") is False
        assert result["risk_label"] == "low"

    def test_weighted_combination(self):
        """Should compute weighted combination correctly."""
        result: dict[str, Any] = {
            "atomic_block_score": 100,
            "common_funder_score": 0,
            "distribution_anomaly_score": 0,
            "concentration_score": 0,
        }
        bd._compute_bundle_score(result)
        # 100 * 0.35 = 35
        assert result["confidence"] == 35.0


# ── Cache Tests ──────────────────────────────────────────────────────────


class TestBundleCache:
    """Tests for bundle cache."""

    def setup_method(self):
        bd.clear_cache()

    def test_cache_starts_empty(self):
        """Cache should start empty."""
        stats = bd.get_cache_stats()
        assert stats["cache_size"] == 0

    def test_cache_max_positive(self):
        """Cache max should be positive."""
        stats = bd.get_cache_stats()
        assert stats["cache_max"] > 0

    def test_cache_ttl_positive(self):
        """Cache TTL should be positive."""
        stats = bd.get_cache_stats()
        assert stats["cache_ttl_seconds"] > 0

    def test_get_cache_stats_returns_keys(self):
        """get_cache_stats should include clickhouse_connected."""
        stats = bd.get_cache_stats()
        assert "clickhouse_connected" in stats
        # Should be False in test environment
        assert stats["clickhouse_connected"] is False
