"""
Tests for the Supply Manipulation / Bundler Detector (bundler_detect.py)
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from bundler_detect import (
    BundledBuy,
    BundlerDetector,
    BundlerReport,
    HolderCluster,
    _entropy,
    _funding_overlap,
    _gini_coefficient,
    _label_risk,
    _time_cluster_similarity,
)


class TestHelpers(unittest.TestCase):
    """Test scoring helper functions."""

    def test_gini_coefficient_equal(self) -> None:
        """Perfectly equal distribution → Gini = 0."""
        vals = [10.0] * 10
        self.assertAlmostEqual(_gini_coefficient(vals), 0.0, places=2)

    def test_gini_coefficient_concentrated(self) -> None:
        """Maximally concentrated → Gini ≈ 0.9 (theoretical max for n=10 with one non-zero)."""
        vals = [100.0] + [0.0] * 9
        self.assertAlmostEqual(_gini_coefficient(vals), 0.9, places=2)

    def test_gini_coefficient_empty(self) -> None:
        """Empty list → Gini = 0."""
        assert _gini_coefficient([]) == 0.0

    def test_gini_coefficient_typical(self) -> None:
        """Typical uneven distribution."""
        vals = [50.0, 20.0, 10.0, 5.0, 5.0, 5.0, 3.0, 1.0, 1.0, 0.0]
        gini = _gini_coefficient(vals)
        assert gini > 0.5
        assert gini < 0.9

    def test_entropy_uniform(self) -> None:
        """Uniform distribution → entropy = 1.0 (normalized)."""
        vals = [10.0] * 4
        self.assertAlmostEqual(_entropy(vals), 1.0, places=2)

    def test_entropy_concentrated(self) -> None:
        """One holder has everything → entropy ≈ 0."""
        vals = [100.0, 0.0, 0.0, 0.0]
        self.assertAlmostEqual(_entropy(vals), 0.0, places=2)

    def test_time_cluster_similarity_all_same(self) -> None:
        """All buys at same timestamp → similarity = 1.0."""
        ts = [1000.0] * 10
        assert _time_cluster_similarity(ts) == 1.0

    def test_time_cluster_similarity_spread(self) -> None:
        """Buys spread over 10 minutes → low similarity."""
        ts = [1000.0, 1600.0]
        assert _time_cluster_similarity(ts) < 0.5

    def test_time_cluster_similarity_single(self) -> None:
        """Single timestamp → 0.0."""
        assert _time_cluster_similarity([1000.0]) == 0.0

    def test_time_cluster_similarity_empty(self) -> None:
        """Empty list → 0.0."""
        assert _time_cluster_similarity([]) == 0.0

    def test_funding_overlap_no_overlap(self) -> None:
        """All unique sources → 0.0."""
        sources = ["src_a", "src_b", "src_c"]
        assert _funding_overlap(sources) == 0.0

    def test_funding_overlap_full_overlap(self) -> None:
        """All same source → 1.0."""
        sources = ["src_x"] * 5
        assert _funding_overlap(sources) == 1.0

    def test_funding_overlap_partial(self) -> None:
        """2 of 4 share a source → 0.5."""
        sources = ["src_a", "src_b", "src_a", "src_c"]
        assert _funding_overlap(sources) == 0.5

    def test_label_risk_critical(self) -> None:
        assert _label_risk(80) == "critical"
        assert _label_risk(75) == "critical"

    def test_label_risk_high(self) -> None:
        assert _label_risk(60) == "high"
        assert _label_risk(50) == "high"

    def test_label_risk_medium(self) -> None:
        assert _label_risk(30) == "medium"
        assert _label_risk(25) == "medium"

    def test_label_risk_low(self) -> None:
        assert _label_risk(10) == "low"
        assert _label_risk(1) == "low"

    def test_label_risk_none(self) -> None:
        assert _label_risk(0) == "none"

class TestDataModels(unittest.TestCase):
    """Test dataclass models."""

    def test_bundler_report_to_dict(self) -> None:
        report = BundlerReport(
            token_address="0x123",
            chain="ethereum",
            name="TestToken",
            symbol="TST",
            bundler_score=85.0,
            supply_concentration_score=90.0,
            sniper_cluster_score=80.0,
            launch_timing_anomaly_score=75.0,
            fund_flow_risk_score=85.5,
            top_10_holder_concentration=92.0,
            dev_hold_pct=35.0,
            estimated_unique_entities=3,
            risk_label="critical",
        )
        d = report.to_dict()
        assert d["token_address"] == "0x123"
        assert d["risk_label"] == "critical"
        assert d["bundler_score"] == 85.0
        assert d["signals"]["supply_concentration"] == 90.0

    def test_bundler_report_summary(self) -> None:
        report = BundlerReport(
            token_address="0x1234567890abcdef12345678",
            chain="ethereum",
            name="TestToken",
            symbol="TST",
            bundler_score=85.0,
            risk_label="critical",
            top_10_holder_concentration=92.0,
            estimated_unique_entities=3,
            holder_clusters=[
                HolderCluster(
                    wallets=["wallet1", "wallet2"],
                    total_supply_pct=45.0,
                    funding_overlap_score=0.8,
                    buy_time_similarity=0.9,
                )
            ],
            buys_from_same_funding=15,
            suspected_bundled_buys=[
                BundledBuy(
                    wallet="wallet1",
                    amount_usd=5000.0,
                    buy_block=12345,
                    buy_timestamp=1000.0,
                    is_sniper=True,
                )
            ],
        )
        summary = report.summary()
        assert "CRITICAL" in summary
        assert "TST" in summary
        assert "85/100" in summary
        assert "1 clusters" in summary
        assert "3 entities" in summary

    def test_bundled_buy_to_dict(self) -> None:
        buy = BundledBuy(
            wallet="abc123",
            amount_usd=5000.0,
            buy_block=100000,
            buy_timestamp=1234567890.0,
            tx_hash="0xdeadbeef",
            funding_source="cex_1",
            is_sniper=True,
        )
        d = buy.to_dict()
        assert d["wallet"] == "abc123"
        assert d["amount_usd"] == 5000.0
        assert d["is_sniper"]

    def test_holder_cluster_to_dict(self) -> None:
        cluster = HolderCluster(
            wallets=["w1", "w2", "w3"],
            total_supply_pct=45.0,
            funding_overlap_score=0.9,
            buy_time_similarity=0.85,
            common_funding_source="cex_shared",
        )
        d = cluster.to_dict()
        assert d["wallet_count"] == 3
        assert d["total_supply_pct"] == 45.0

class TestBundlerDetector(unittest.TestCase):
    """Test main BundlerDetector class."""

    def setUp(self) -> None:
        self.detector = BundlerDetector()

    def tearDown(self) -> None:
        asyncio.run(self.detector.close())

    # ── Address Validation ──────────────────────────

    def test_validate_address_evm(self) -> None:
        assert self.detector._validate_address("0x1234567890123456789012345678901234567890", "ethereum")
        assert not self.detector._validate_address("invalid_address", "ethereum")

    def test_validate_address_solana(self) -> None:
        valid = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLsLvDt2BQHpM"
        assert self.detector._validate_address(valid, "solana")
        assert not self.detector._validate_address("0xshort", "solana")

    def test_validate_address_unsupported_chain(self) -> None:
        """Address format validation is chain-agnostic; scan() rejects unsupported chains."""
        # Format validation passes for any valid EVM address regardless of chain string
        assert self.detector._validate_address("0x1234567890123456789012345678901234567890", "bitcoin")
        # But scan() will reject unsupported chains
        report = asyncio.run(
            self.detector.scan("0x1234567890123456789012345678901234567890", "bitcoin")
        )
        assert report.risk_label == "error"
        assert "unsupported chain" in str(report.errors).lower()

    # ── Supply Concentration ────────────────────────

    def test_compute_top_holder_pct(self) -> None:
        holders = [
            {"address": "a1", "percentage": 50.0},
            {"address": "a2", "percentage": 30.0},
            {"address": "a3", "percentage": 15.0},
            {"address": "a4", "percentage": 5.0},
        ]
        assert self.detector._compute_top_holder_pct(holders, 3) == 95.0
        assert self.detector._compute_top_holder_pct(holders, 1) == 50.0

    def test_compute_top_holder_pct_empty(self) -> None:
        assert self.detector._compute_top_holder_pct([], 10) == 0.0

    def test_extract_dev_hold_pct(self) -> None:
        holders = [{"address": "dev", "percentage": 25.0}]
        assert self.detector._extract_dev_hold_pct(holders, {}) == 25.0

    def test_extract_dev_hold_pct_empty(self) -> None:
        assert self.detector._extract_dev_hold_pct([], {}) == 0.0

    # ── Clustering ─────────────────────────────────

    def test_cluster_wallets_top_only(self) -> None:
        """Top 3 holders controlling >60% form a cluster."""
        holders = [
            {"address": "whale1", "percentage": 35.0},
            {"address": "whale2", "percentage": 25.0},
            {"address": "whale3", "percentage": 15.0},
            {"address": "small", "percentage": 2.0},
        ]
        clusters = self.detector._cluster_wallets([], holders)
        assert len(clusters) == 1
        assert "top_holders_cluster" in clusters[0].common_funding_source

    def test_cluster_wallets_middle_belt(self) -> None:
        """5+ holders with 2-15% each form a mid-holder belt cluster."""
        holders = [{"address": f"mid{i}", "percentage": 4.0} for i in range(10)]
        clusters = self.detector._cluster_wallets([], holders)
        assert len(clusters) == 1
        assert "mid_holder_belt" in clusters[0].common_funding_source

    def test_cluster_wallets_empty(self) -> None:
        assert self.detector._cluster_wallets([], []) == []

    # ── Entity Estimation ──────────────────────────

    def test_estimate_entities(self) -> None:
        clusters = [
            HolderCluster(
                wallets=["w1", "w2", "w3"],
                total_supply_pct=60.0,
                funding_overlap_score=0.8,
                buy_time_similarity=0.9,
            )
        ]
        entities = self.detector._estimate_entities(
            [{"address": f"h{i}"} for i in range(20)],
            clusters,
            0,
        )
        # 20 holders - 3 in cluster = 17 + 1 for the cluster
        assert entities == 17

    # ── Scoring ────────────────────────────────────

    def test_score_supply_concentration_critical(self) -> None:
        holders = [{"percentage": p} for p in [90.0, 5.0, 2.0, 1.0, 1.0, 0.5, 0.3, 0.1, 0.05, 0.05]]
        score = self.detector._score_supply_concentration(holders, 100.0)
        assert score >= 75

    def test_score_supply_concentration_low(self) -> None:
        holders = [{"percentage": p} for p in [10.0, 8.0, 7.0, 6.0, 5.0, 5.0, 5.0, 4.0, 4.0, 4.0]]
        score = self.detector._score_supply_concentration(holders, 58.0)
        assert score < 75

    def test_compute_bundler_score_weights(self) -> None:
        report = BundlerReport(
            token_address="0x123",
            chain="ethereum",
            supply_concentration_score=100.0,
            sniper_cluster_score=100.0,
            launch_timing_anomaly_score=100.0,
            fund_flow_risk_score=100.0,
        )
        score = self.detector._compute_bundler_score(report)
        assert score == 100.0

    def test_compute_bundler_score_half(self) -> None:
        report = BundlerReport(
            token_address="0x123",
            chain="ethereum",
            supply_concentration_score=50.0,
            sniper_cluster_score=50.0,
            launch_timing_anomaly_score=50.0,
            fund_flow_risk_score=50.0,
        )
        score = self.detector._compute_bundler_score(report)
        assert score == 50.0

    # ── Launch Timing ──────────────────────────────

    def test_analyze_launch_timing_high_concentration(self) -> None:
        buys = [
            {"m5_buys": 80, "h1_buys": 20, "h6_buys": 5, "pair_address": "0xpair"},
        ]
        result = self.detector._analyze_launch_timing(buys)
        assert result["buy_concentration_ratio"] > 0.4

    def test_analyze_launch_timing_empty(self) -> None:
        result = self.detector._analyze_launch_timing([])
        assert result["total_buys_first_blocks"] == 0

    # ── Quick Check ────────────────────────────────

    def test_quick_check_invalid_address(self) -> None:
        result = asyncio.run(self.detector.quick_check("bad", "ethereum"))
        assert "error" in result

    def test_quick_check_no_holders(self) -> None:
        with (
            patch.object(self.detector, "_fetch_holders", return_value=[]),
            patch.object(
                self.detector, "_fetch_metadata", return_value={"name": "TST", "symbol": "TST"}
            ),
        ):
            result = asyncio.run(
                self.detector.quick_check("0x1234567890123456789012345678901234567890", "ethereum")
            )
            assert "error" in result

class TestEdgeCases(unittest.TestCase):
    """Test edge cases for robustness."""

    def test_gini_single_value(self) -> None:
        """Single value should not crash."""
        self.assertAlmostEqual(_gini_coefficient([100.0]), 0.0, places=2)

    def test_entropy_single_value(self) -> None:
        """Single value → entropy = 1.0 (trivially uniform)."""
        self.assertAlmostEqual(_entropy([100.0]), 1.0, places=2)

    def test_entropy_all_zeros(self) -> None:
        """All zeros → 0.0."""
        assert _entropy([0.0] * 5) == 0.0

    def test_time_cluster_similarity_two_fast(self) -> None:
        """Two buys within 1 second → high similarity."""
        assert _time_cluster_similarity([1000.0, 1001.0]) > 0.8

    def test_time_cluster_similarity_wide(self) -> None:
        """Buys spread over 1 hour → very low similarity."""
        ts = [0.0, 600.0, 1200.0, 1800.0, 3600.0]
        assert _time_cluster_similarity(ts) < 0.2

    def test_funding_overlap_empty(self) -> None:
        """Empty list → 0.0."""
        assert _funding_overlap([]) == 0.0

    def test_funding_overlap_single(self) -> None:
        """Single source → 0.0."""
        assert _funding_overlap(["only_source"]) == 0.0

    def test_bundler_report_no_clusters(self) -> None:
        """Report without clusters should not crash on summary."""
        report = BundlerReport(
            token_address="0xabc",
            chain="solana",
        )
        s = report.summary()
        assert "NONE" in s
        assert "0 clusters" in s

    def test_launch_timing_handles_missing_keys(self) -> None:
        """Buys with missing keys should not crash."""
        detector = BundlerDetector()
        buys = [
            {"m5_buys": 10},  # missing h1_buys, h6_buys
        ]
        result = detector._analyze_launch_timing(buys)
        assert result["total_buys_first_blocks"] == 10

if __name__ == "__main__":
    unittest.main()
