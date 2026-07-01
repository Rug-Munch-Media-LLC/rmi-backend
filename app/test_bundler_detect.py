"""
Tests for the Supply Manipulation / Bundler Detector (bundler_detect.py)
"""

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

    def test_gini_coefficient_equal(self):
        """Perfectly equal distribution → Gini = 0."""
        vals = [10.0] * 10
        self.assertAlmostEqual(_gini_coefficient(vals), 0.0, places=2)

    def test_gini_coefficient_concentrated(self):
        """Maximally concentrated → Gini ≈ 0.9 (theoretical max for n=10 with one non-zero)."""
        vals = [100.0] + [0.0] * 9
        self.assertAlmostEqual(_gini_coefficient(vals), 0.9, places=2)

    def test_gini_coefficient_empty(self):
        """Empty list → Gini = 0."""
        self.assertEqual(_gini_coefficient([]), 0.0)

    def test_gini_coefficient_typical(self):
        """Typical uneven distribution."""
        vals = [50.0, 20.0, 10.0, 5.0, 5.0, 5.0, 3.0, 1.0, 1.0, 0.0]
        gini = _gini_coefficient(vals)
        self.assertGreater(gini, 0.5)
        self.assertLess(gini, 0.9)

    def test_entropy_uniform(self):
        """Uniform distribution → entropy = 1.0 (normalized)."""
        vals = [10.0] * 4
        self.assertAlmostEqual(_entropy(vals), 1.0, places=2)

    def test_entropy_concentrated(self):
        """One holder has everything → entropy ≈ 0."""
        vals = [100.0, 0.0, 0.0, 0.0]
        self.assertAlmostEqual(_entropy(vals), 0.0, places=2)

    def test_time_cluster_similarity_all_same(self):
        """All buys at same timestamp → similarity = 1.0."""
        ts = [1000.0] * 10
        self.assertEqual(_time_cluster_similarity(ts), 1.0)

    def test_time_cluster_similarity_spread(self):
        """Buys spread over 10 minutes → low similarity."""
        ts = [1000.0, 1600.0]
        self.assertLess(_time_cluster_similarity(ts), 0.5)

    def test_time_cluster_similarity_single(self):
        """Single timestamp → 0.0."""
        self.assertEqual(_time_cluster_similarity([1000.0]), 0.0)

    def test_time_cluster_similarity_empty(self):
        """Empty list → 0.0."""
        self.assertEqual(_time_cluster_similarity([]), 0.0)

    def test_funding_overlap_no_overlap(self):
        """All unique sources → 0.0."""
        sources = ["src_a", "src_b", "src_c"]
        self.assertEqual(_funding_overlap(sources), 0.0)

    def test_funding_overlap_full_overlap(self):
        """All same source → 1.0."""
        sources = ["src_x"] * 5
        self.assertEqual(_funding_overlap(sources), 1.0)

    def test_funding_overlap_partial(self):
        """2 of 4 share a source → 0.5."""
        sources = ["src_a", "src_b", "src_a", "src_c"]
        self.assertEqual(_funding_overlap(sources), 0.5)

    def test_label_risk_critical(self):
        self.assertEqual(_label_risk(80), "critical")
        self.assertEqual(_label_risk(75), "critical")

    def test_label_risk_high(self):
        self.assertEqual(_label_risk(60), "high")
        self.assertEqual(_label_risk(50), "high")

    def test_label_risk_medium(self):
        self.assertEqual(_label_risk(30), "medium")
        self.assertEqual(_label_risk(25), "medium")

    def test_label_risk_low(self):
        self.assertEqual(_label_risk(10), "low")
        self.assertEqual(_label_risk(1), "low")

    def test_label_risk_none(self):
        self.assertEqual(_label_risk(0), "none")


class TestDataModels(unittest.TestCase):
    """Test dataclass models."""

    def test_bundler_report_to_dict(self):
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
        self.assertEqual(d["token_address"], "0x123")
        self.assertEqual(d["risk_label"], "critical")
        self.assertEqual(d["bundler_score"], 85.0)
        self.assertEqual(d["signals"]["supply_concentration"], 90.0)

    def test_bundler_report_summary(self):
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
        self.assertIn("CRITICAL", summary)
        self.assertIn("TST", summary)
        self.assertIn("85/100", summary)
        self.assertIn("1 clusters", summary)
        self.assertIn("3 entities", summary)

    def test_bundled_buy_to_dict(self):
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
        self.assertEqual(d["wallet"], "abc123")
        self.assertEqual(d["amount_usd"], 5000.0)
        self.assertTrue(d["is_sniper"])

    def test_holder_cluster_to_dict(self):
        cluster = HolderCluster(
            wallets=["w1", "w2", "w3"],
            total_supply_pct=45.0,
            funding_overlap_score=0.9,
            buy_time_similarity=0.85,
            common_funding_source="cex_shared",
        )
        d = cluster.to_dict()
        self.assertEqual(d["wallet_count"], 3)
        self.assertEqual(d["total_supply_pct"], 45.0)


class TestBundlerDetector(unittest.TestCase):
    """Test main BundlerDetector class."""

    def setUp(self):
        self.detector = BundlerDetector()

    def tearDown(self):
        asyncio.run(self.detector.close())

    # ── Address Validation ──────────────────────────

    def test_validate_address_evm(self):
        self.assertTrue(self.detector._validate_address("0x1234567890123456789012345678901234567890", "ethereum"))
        self.assertFalse(self.detector._validate_address("invalid_address", "ethereum"))

    def test_validate_address_solana(self):
        valid = "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLsLvDt2BQHpM"
        self.assertTrue(self.detector._validate_address(valid, "solana"))
        self.assertFalse(self.detector._validate_address("0xshort", "solana"))

    def test_validate_address_unsupported_chain(self):
        """Address format validation is chain-agnostic; scan() rejects unsupported chains."""
        # Format validation passes for any valid EVM address regardless of chain string
        self.assertTrue(self.detector._validate_address("0x1234567890123456789012345678901234567890", "bitcoin"))
        # But scan() will reject unsupported chains
        report = asyncio.run(self.detector.scan("0x1234567890123456789012345678901234567890", "bitcoin"))
        self.assertEqual(report.risk_label, "error")
        self.assertIn("unsupported chain", str(report.errors).lower())

    # ── Supply Concentration ────────────────────────

    def test_compute_top_holder_pct(self):
        holders = [
            {"address": "a1", "percentage": 50.0},
            {"address": "a2", "percentage": 30.0},
            {"address": "a3", "percentage": 15.0},
            {"address": "a4", "percentage": 5.0},
        ]
        self.assertEqual(self.detector._compute_top_holder_pct(holders, 3), 95.0)
        self.assertEqual(self.detector._compute_top_holder_pct(holders, 1), 50.0)

    def test_compute_top_holder_pct_empty(self):
        self.assertEqual(self.detector._compute_top_holder_pct([], 10), 0.0)

    def test_extract_dev_hold_pct(self):
        holders = [{"address": "dev", "percentage": 25.0}]
        self.assertEqual(self.detector._extract_dev_hold_pct(holders, {}), 25.0)

    def test_extract_dev_hold_pct_empty(self):
        self.assertEqual(self.detector._extract_dev_hold_pct([], {}), 0.0)

    # ── Clustering ─────────────────────────────────

    def test_cluster_wallets_top_only(self):
        """Top 3 holders controlling >60% form a cluster."""
        holders = [
            {"address": "whale1", "percentage": 35.0},
            {"address": "whale2", "percentage": 25.0},
            {"address": "whale3", "percentage": 15.0},
            {"address": "small", "percentage": 2.0},
        ]
        clusters = self.detector._cluster_wallets([], holders)
        self.assertEqual(len(clusters), 1)
        self.assertIn("top_holders_cluster", clusters[0].common_funding_source)

    def test_cluster_wallets_middle_belt(self):
        """5+ holders with 2-15% each form a mid-holder belt cluster."""
        holders = [{"address": f"mid{i}", "percentage": 4.0} for i in range(10)]
        clusters = self.detector._cluster_wallets([], holders)
        self.assertEqual(len(clusters), 1)
        self.assertIn("mid_holder_belt", clusters[0].common_funding_source)

    def test_cluster_wallets_empty(self):
        self.assertEqual(self.detector._cluster_wallets([], []), [])

    # ── Entity Estimation ──────────────────────────

    def test_estimate_entities(self):
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
        self.assertEqual(entities, 17)

    # ── Scoring ────────────────────────────────────

    def test_score_supply_concentration_critical(self):
        holders = [{"percentage": p} for p in [90.0, 5.0, 2.0, 1.0, 1.0, 0.5, 0.3, 0.1, 0.05, 0.05]]
        score = self.detector._score_supply_concentration(holders, 100.0)
        self.assertGreaterEqual(score, 75)

    def test_score_supply_concentration_low(self):
        holders = [{"percentage": p} for p in [10.0, 8.0, 7.0, 6.0, 5.0, 5.0, 5.0, 4.0, 4.0, 4.0]]
        score = self.detector._score_supply_concentration(holders, 58.0)
        self.assertLess(score, 75)

    def test_compute_bundler_score_weights(self):
        report = BundlerReport(
            token_address="0x123",
            chain="ethereum",
            supply_concentration_score=100.0,
            sniper_cluster_score=100.0,
            launch_timing_anomaly_score=100.0,
            fund_flow_risk_score=100.0,
        )
        score = self.detector._compute_bundler_score(report)
        self.assertEqual(score, 100.0)

    def test_compute_bundler_score_half(self):
        report = BundlerReport(
            token_address="0x123",
            chain="ethereum",
            supply_concentration_score=50.0,
            sniper_cluster_score=50.0,
            launch_timing_anomaly_score=50.0,
            fund_flow_risk_score=50.0,
        )
        score = self.detector._compute_bundler_score(report)
        self.assertEqual(score, 50.0)

    # ── Launch Timing ──────────────────────────────

    def test_analyze_launch_timing_high_concentration(self):
        buys = [
            {"m5_buys": 80, "h1_buys": 20, "h6_buys": 5, "pair_address": "0xpair"},
        ]
        result = self.detector._analyze_launch_timing(buys)
        self.assertGreater(result["buy_concentration_ratio"], 0.4)

    def test_analyze_launch_timing_empty(self):
        result = self.detector._analyze_launch_timing([])
        self.assertEqual(result["total_buys_first_blocks"], 0)

    # ── Quick Check ────────────────────────────────

    def test_quick_check_invalid_address(self):
        result = asyncio.run(self.detector.quick_check("bad", "ethereum"))
        self.assertIn("error", result)

    def test_quick_check_no_holders(self):
        with (
            patch.object(self.detector, "_fetch_holders", return_value=[]),
            patch.object(self.detector, "_fetch_metadata", return_value={"name": "TST", "symbol": "TST"}),
        ):
            result = asyncio.run(self.detector.quick_check("0x1234567890123456789012345678901234567890", "ethereum"))
            self.assertIn("error", result)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases for robustness."""

    def test_gini_single_value(self):
        """Single value should not crash."""
        self.assertAlmostEqual(_gini_coefficient([100.0]), 0.0, places=2)

    def test_entropy_single_value(self):
        """Single value → entropy = 1.0 (trivially uniform)."""
        self.assertAlmostEqual(_entropy([100.0]), 1.0, places=2)

    def test_entropy_all_zeros(self):
        """All zeros → 0.0."""
        self.assertEqual(_entropy([0.0] * 5), 0.0)

    def test_time_cluster_similarity_two_fast(self):
        """Two buys within 1 second → high similarity."""
        self.assertGreater(_time_cluster_similarity([1000.0, 1001.0]), 0.8)

    def test_time_cluster_similarity_wide(self):
        """Buys spread over 1 hour → very low similarity."""
        ts = [0.0, 600.0, 1200.0, 1800.0, 3600.0]
        self.assertLess(_time_cluster_similarity(ts), 0.2)

    def test_funding_overlap_empty(self):
        """Empty list → 0.0."""
        self.assertEqual(_funding_overlap([]), 0.0)

    def test_funding_overlap_single(self):
        """Single source → 0.0."""
        self.assertEqual(_funding_overlap(["only_source"]), 0.0)

    def test_bundler_report_no_clusters(self):
        """Report without clusters should not crash on summary."""
        report = BundlerReport(
            token_address="0xabc",
            chain="solana",
        )
        s = report.summary()
        self.assertIn("NONE", s)
        self.assertIn("0 clusters", s)

    def test_launch_timing_handles_missing_keys(self):
        """Buys with missing keys should not crash."""
        detector = BundlerDetector()
        buys = [
            {"m5_buys": 10},  # missing h1_buys, h6_buys
        ]
        result = detector._analyze_launch_timing(buys)
        self.assertEqual(result["total_buys_first_blocks"], 10)


if __name__ == "__main__":
    unittest.main()
