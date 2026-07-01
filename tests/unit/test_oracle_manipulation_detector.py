"""
Tests for Oracle Manipulation Detector
=======================================
Covers core detection heuristics: TWAP manipulation, Chainlink staleness,
flash loan-backed manipulation, cross-pool divergence, severity
classification, sandwich detection, and data model serialization —
all without requiring network calls.
"""

import json
import unittest

from app.oracle_manipulation_detector import (
    CHAINLINK_FEEDS,
    CROSS_POOL_DIVERGENCE_THRESHOLD,
    KNOWN_LP_POOLS,
    MIN_TWAP_SAMPLES,
    TWAP_MANIPULATION_THRESHOLD_PCT,
    ManipulationType,
    OracleManipulationDetector,
    OracleRead,
    OracleType,
    PriceManipulation,
    PriceSnapshot,
    Severity,
    _calculate_twap_from_samples,
    _classify_severity,
    _compute_deviation_pct,
    _detect_twap_manipulation,
    _is_sandwich_pattern,
)


class TestPriceSnapshot(unittest.TestCase):
    """PriceSnapshot data model tests."""

    def test_creation(self):
        snap = PriceSnapshot(
            timestamp=1234567890.0,
            block_number=20000000,
            price=3200.50,
            liquidity=10000000,
            source="Uniswap V3",
            chain="ethereum",
        )
        self.assertEqual(snap.price, 3200.50)
        self.assertEqual(snap.block_number, 20000000)
        self.assertEqual(snap.chain, "ethereum")

    def test_to_dict(self):
        snap = PriceSnapshot(
            timestamp=1234567890.0,
            block_number=20000000,
            price=3200.50,
        )
        d = snap.to_dict()
        self.assertEqual(d["price"], 3200.50)
        self.assertEqual(d["block_number"], 20000000)
        self.assertEqual(d["chain"], "ethereum")  # default


class TestOracleRead(unittest.TestCase):
    """OracleRead data model and staleness detection tests."""

    def test_fresh_feed(self):
        read = OracleRead(
            tx_hash="0xabc",
            block_number=20000000,
            timestamp=1234567890.0,
            oracle_address="0xdead",
            oracle_type=OracleType.CHAINLINK,
            reported_price=3200.0,
            expected_price=3200.0,
            price_age_seconds=300,  # 5 min — fresh
        )
        self.assertFalse(read.is_stale())

    def test_stale_feed(self):
        read = OracleRead(
            tx_hash="0xabc",
            block_number=20000000,
            timestamp=1234567890.0,
            oracle_address="0xdead",
            oracle_type=OracleType.CHAINLINK,
            reported_price=3200.0,
            expected_price=3200.0,
            price_age_seconds=86400,  # 24 hours — very stale
        )
        self.assertTrue(read.is_stale())

    def test_deviation_calculation(self):
        read = OracleRead(
            tx_hash="0xabc",
            block_number=20000000,
            timestamp=1234567890.0,
            oracle_address="0xdead",
            oracle_type=OracleType.CHAINLINK,
            reported_price=3500.0,
            expected_price=3200.0,
        )
        deviation = read.deviation_from_expected()
        self.assertIsNotNone(deviation)
        self.assertAlmostEqual(deviation, 0.09375, places=5)  # 9.375%

    def test_deviation_none_when_no_expected(self):
        read = OracleRead(
            tx_hash="0xabc",
            block_number=20000000,
            timestamp=1234567890.0,
            oracle_address="0xdead",
            oracle_type=OracleType.CHAINLINK,
            reported_price=3200.0,
        )
        self.assertIsNone(read.deviation_from_expected())

    def test_to_dict(self):
        read = OracleRead(
            tx_hash="0xabc",
            block_number=20000000,
            timestamp=1234567890.0,
            oracle_address="0xdead",
            oracle_type=OracleType.CHAINLINK,
            reported_price=3200.0,
            expected_price=3200.0,
            price_age_seconds=300,
            chain="ethereum",
            protocol="Chainlink",
        )
        d = read.to_dict()
        self.assertEqual(d["oracle_type"], "chainlink")
        self.assertFalse(d["is_stale"])
        self.assertEqual(d["deviation_pct"], 0.0)

    def test_to_dict_no_expected(self):
        read = OracleRead(
            tx_hash="0xabc",
            block_number=20000000,
            timestamp=1234567890.0,
            oracle_address="0xdead",
            oracle_type=OracleType.CHAINLINK,
            reported_price=3200.0,
        )
        d = read.to_dict()
        self.assertIsNone(d["expected_price"])
        self.assertIsNone(d["deviation_pct"])


class TestOracleType(unittest.TestCase):
    """OracleType enum tests."""

    def test_from_string(self):
        self.assertEqual(OracleType.from_string("chainlink"), OracleType.CHAINLINK)
        self.assertEqual(OracleType.from_string("uniswap_v3_twap"), OracleType.UNISWAP_V3_TWAP)
        self.assertEqual(OracleType.from_string("uniswap_v2_twap"), OracleType.UNISWAP_V2_TWAP)
        self.assertEqual(OracleType.from_string("curve_ema"), OracleType.CURVE_EMA)
        self.assertEqual(OracleType.from_string("unknown_foo"), OracleType.CUSTOM)
        self.assertEqual(OracleType.from_string(""), OracleType.CUSTOM)


class TestSeverity(unittest.TestCase):
    """Severity enum ordering and score tests."""

    def test_severity_ordering(self):
        self.assertLess(Severity.INFO, Severity.LOW)
        self.assertLess(Severity.LOW, Severity.MEDIUM)
        self.assertLess(Severity.MEDIUM, Severity.HIGH)
        self.assertLess(Severity.HIGH, Severity.CRITICAL)

    def test_severity_scores(self):
        self.assertEqual(Severity.CRITICAL.score, 1.0)
        self.assertEqual(Severity.INFO.score, 0.0)
        self.assertGreater(Severity.HIGH.score, Severity.MEDIUM.score)


class TestManipulationType(unittest.TestCase):
    """ManipulationType label tests."""

    def test_label_format(self):
        mt = ManipulationType.TWAP_POISONING
        self.assertEqual(mt.value, "twap_poisoning")


class TestPriceManipulation(unittest.TestCase):
    """PriceManipulation data model tests."""

    def setUp(self):
        self.incident = PriceManipulation(
            manipulation_type=ManipulationType.TWAP_POISONING,
            severity=Severity.HIGH,
            chain="ethereum",
            block_number=20000000,
            tx_hash="0xdeadbeef",
            timestamp=1234567890.0,
            pool_address="0xpool",
            pair="WETH/USDC",
            oracle_type=OracleType.UNISWAP_V3_TWAP,
            observed_price=3400.0,
            expected_price=3200.0,
            deviation_pct=6.25,
            description="TWAP manipulation detected",
            evidence=["std_dev=0.03", "risk=0.75"],
        )

    def test_summary_contains_info(self):
        s = self.incident.summary()
        self.assertIn("HIGH", s)
        self.assertIn("TWAP", s)
        self.assertIn("WETH/USDC", s)
        self.assertIn("6.25", s)

    def test_label(self):
        self.assertEqual(self.incident.label, "Twap Poisoning")

    def test_to_dict(self):
        d = self.incident.to_dict()
        self.assertEqual(d["type"], "twap_poisoning")
        self.assertEqual(d["severity"], "high")
        self.assertEqual(d["deviation_pct"], 6.25)
        self.assertFalse(d["external_fatigue"])


class TestTWAPWindow(unittest.TestCase):
    """TWAPWindow computation and risk scoring tests."""

    def setUp(self):
        # Create stable price samples
        self.stable_samples = [
            PriceSnapshot(
                timestamp=1234567800.0 + i * 12,
                block_number=20000000 + i,
                price=3200.0,
                liquidity=10000000,
            )
            for i in range(10)
        ]

        # Create volatile price samples (manipulation pattern)
        self.volatile_samples = []
        for i in range(10):
            if i in (3, 4, 5):  # Spike in middle
                price = 3700.0
            else:
                price = 3200.0
            self.volatile_samples.append(
                PriceSnapshot(
                    timestamp=1234567800.0 + i * 12,
                    block_number=20000000 + i,
                    price=price,
                    liquidity=10000000,
                )
            )

    def test_stable_twap(self):
        window = _calculate_twap_from_samples(self.stable_samples)
        self.assertIsNotNone(window)
        self.assertEqual(window.average_price, 3200.0)
        self.assertEqual(window.min_price, 3200.0)
        self.assertEqual(window.max_price, 3200.0)
        self.assertEqual(window.std_dev_pct, 0.0)
        # 0 std dev + 10 samples = 0 manipulation risk
        self.assertEqual(window.manipulation_risk(), 0.0)

    def test_volatile_twap(self):
        window = _calculate_twap_from_samples(self.volatile_samples)
        self.assertIsNotNone(window)
        self.assertGreater(window.average_price, 3200.0)
        self.assertGreater(window.std_dev_pct, 0.01)
        self.assertGreater(window.manipulation_risk(), 0.2)

    def test_twap_manipulation_detection(self):
        # Compute TWAP from volatile samples
        window = _calculate_twap_from_samples(self.volatile_samples)
        self.assertIsNotNone(window)

        incident = _detect_twap_manipulation(window)
        # High volatility should trigger detection
        self.assertIsNotNone(incident)
        self.assertEqual(incident.manipulation_type, ManipulationType.TWAP_POISONING)
        self.assertIn(incident.severity, [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW])

    def test_insufficient_samples(self):
        samples = [PriceSnapshot(timestamp=1.0, block_number=1, price=100.0)]
        self.assertIsNone(_calculate_twap_from_samples(samples))

    def test_twap_to_dict(self):
        window = _calculate_twap_from_samples(self.stable_samples)
        d = window.to_dict()
        self.assertIn("average_price", d)
        self.assertIn("manipulation_risk", d)
        self.assertIn("samples", d)
        self.assertEqual(d["samples"], 10)


class TestHeuristicHelpers(unittest.TestCase):
    """Test standalone heuristic functions."""

    def test_compute_deviation_pct(self):
        self.assertAlmostEqual(_compute_deviation_pct(3200.0, 3200.0), 0.0)
        self.assertAlmostEqual(_compute_deviation_pct(3400.0, 3200.0), 0.0625, places=4)
        self.assertAlmostEqual(_compute_deviation_pct(3000.0, 3200.0), 0.0625, places=4)

    def test_compute_deviation_zero(self):
        self.assertEqual(_compute_deviation_pct(100.0, 0.0), 0.0)

    def test_classify_severity_normal(self):
        self.assertEqual(_classify_severity(0.01), Severity.INFO)
        self.assertEqual(_classify_severity(0.04), Severity.LOW)
        self.assertEqual(_classify_severity(0.09), Severity.MEDIUM)
        self.assertEqual(_classify_severity(0.20), Severity.HIGH)
        self.assertEqual(_classify_severity(0.50), Severity.CRITICAL)

    def test_classify_severity_flash_loan(self):
        # Flash loan thresholds are tighter
        self.assertEqual(_classify_severity(0.04, is_flash_loan=True), Severity.INFO)
        self.assertEqual(_classify_severity(0.06, is_flash_loan=True), Severity.MEDIUM)
        self.assertEqual(_classify_severity(0.12, is_flash_loan=True), Severity.HIGH)
        self.assertEqual(_classify_severity(0.25, is_flash_loan=True), Severity.CRITICAL)

    def test_sandwich_pattern_detection(self):
        # Classic sandwich: low → high → low
        prices = [
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
            110.0,
            110.0,  # spike
            100.0,
            100.0,
            100.0,
            100.0,
            100.0,
        ]
        self.assertTrue(_is_sandwich_pattern(prices, window=3))

    def test_no_sandwich_pattern(self):
        # Flat prices
        prices = [100.0] * 15
        self.assertFalse(_is_sandwich_pattern(prices))

    def test_sandwich_too_few_samples(self):
        self.assertFalse(_is_sandwich_pattern([100.0, 100.0], window=5))


class TestOracleManipulationDetector(unittest.TestCase):
    """Integration tests for OracleManipulationDetector."""

    def setUp(self):
        self.detector = OracleManipulationDetector(chain="ethereum")

    def test_scan_produces_report(self):
        """Full scan should produce a valid report structure."""
        import asyncio

        report = asyncio.run(self.detector.scan(blocks_back=30))
        self.assertIsNotNone(report)
        self.assertGreater(report.blocks_scanned, 0)
        self.assertGreater(len(report.scan_id), 0)

    def test_scan_checks_pools(self):
        """Scan should check some LP pools."""
        import asyncio

        report = asyncio.run(self.detector.scan(blocks_back=30))
        self.assertGreater(len(report.twap_windows), 0)
        self.assertEqual(len(report.incidents) + len(report.errors), report.total_incidents + len(report.errors))

    def test_pool_analysis(self):
        """Pool analysis should produce a TWAP window."""
        import asyncio

        report = asyncio.run(
            self.detector.analyze_pool(
                "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
                "WETH/USDC",
            )
        )
        self.assertIsNotNone(report)
        self.assertEqual(report.pools_checked, 1)
        if report.twap_windows:
            twap = report.twap_windows[0]
            self.assertGreater(twap.average_price, 0)

    def test_chainlink_feed_check(self):
        """Chainlink feed check should return feeds."""
        import asyncio

        reads = asyncio.run(self.detector.check_chainlink_feeds(["ETH/USD", "BTC/USD"]))
        self.assertGreater(len(reads), 0)
        for read in reads:
            self.assertEqual(read.oracle_type, OracleType.CHAINLINK)
            self.assertGreater(read.reported_price, 0)

    def test_multichain_scan(self):
        """Multi-chain scan should work."""
        import asyncio

        report = asyncio.run(
            self.detector.scan(
                blocks_back=10,
                chains=["ethereum", "base"],
            )
        )
        self.assertIsNotNone(report)
        self.assertIn("ethereum", report.chain)
        self.assertIn("base", report.chain)

    def test_report_json_serialization(self):
        """Report should serialize to valid JSON."""
        import asyncio

        report = asyncio.run(self.detector.scan(blocks_back=10))
        json_str = report.json()
        data = json.loads(json_str)
        self.assertIn("scan_id", data)
        self.assertIn("incidents", data)
        self.assertIn("twap_windows", data)


class TestPriceManipulationReport(unittest.TestCase):
    """ManipulationReport aggregation tests."""

    def test_risk_score_calculation(self):
        from app.oracle_manipulation_detector import ManipulationReport

        report = ManipulationReport(
            scan_id="test",
            chain="ethereum",
            blocks_scanned=50,
            start_time=0.0,
            end_time=1.0,
        )
        self.assertEqual(report.risk_score, 0.0)

        report.incidents.append(
            PriceManipulation(
                manipulation_type=ManipulationType.TWAP_POISONING,
                severity=Severity.CRITICAL,
                chain="ethereum",
                block_number=1,
            )
        )
        self.assertEqual(report.risk_score, 1.0)

    def test_incident_counts(self):
        from app.oracle_manipulation_detector import ManipulationReport

        report = ManipulationReport(
            scan_id="test",
            chain="ethereum",
            blocks_scanned=50,
            start_time=0.0,
            end_time=1.0,
        )
        report.incidents.append(
            PriceManipulation(
                manipulation_type=ManipulationType.TWAP_POISONING,
                severity=Severity.CRITICAL,
                chain="ethereum",
                block_number=1,
            )
        )
        report.incidents.append(
            PriceManipulation(
                manipulation_type=ManipulationType.CHAINLINK_STALE,
                severity=Severity.HIGH,
                chain="ethereum",
                block_number=2,
            )
        )
        report.incidents.append(
            PriceManipulation(
                manipulation_type=ManipulationType.LP_PRICE_DIVERGENCE,
                severity=Severity.LOW,
                chain="ethereum",
                block_number=3,
            )
        )

        self.assertEqual(report.critical_count, 1)
        self.assertEqual(report.high_count, 1)
        self.assertEqual(report.total_incidents, 3)

    def test_duration(self):
        from app.oracle_manipulation_detector import ManipulationReport

        report = ManipulationReport(
            scan_id="test",
            chain="ethereum",
            blocks_scanned=50,
            start_time=100.0,
            end_time=150.0,
        )
        self.assertEqual(report.duration_seconds, 50.0)


class TestKnownConstants(unittest.TestCase):
    """Test that KNOWN_LP_POOLS and CHAINLINK_FEEDS are well-formed."""

    def test_known_lp_pools_have_expected_structure(self):
        for _pair, pools in KNOWN_LP_POOLS.items():
            for pool in pools:
                self.assertIn("protocol", pool)
                self.assertIn("address", pool)
                self.assertIn(
                    pool["protocol"],
                    [
                        "Uniswap V3",
                        "Uniswap V2",
                        "Curve",
                        "Balancer",
                    ],
                )

    def test_chainlink_feeds_have_required_fields(self):
        for _name, feed in CHAINLINK_FEEDS.items():
            self.assertIn("address", feed)
            self.assertIn("decimals", feed)
            self.assertIn("heartbeat", feed)
            self.assertGreater(feed["decimals"], 0)

    def test_MIN_TWAP_SAMPLES_reasonable(self):
        self.assertGreaterEqual(MIN_TWAP_SAMPLES, 2)
        self.assertLessEqual(MIN_TWAP_SAMPLES, 10)

    def test_cross_pool_divergence_threshold_positive(self):
        self.assertGreater(CROSS_POOL_DIVERGENCE_THRESHOLD, 0)
        self.assertLess(CROSS_POOL_DIVERGENCE_THRESHOLD, 0.5)

    def test_twap_manipulation_threshold_positive(self):
        self.assertGreater(TWAP_MANIPULATION_THRESHOLD_PCT, 0)
        self.assertLess(TWAP_MANIPULATION_THRESHOLD_PCT, 0.5)


class TestEdgeCases(unittest.TestCase):
    """Edge case and boundary tests."""

    def test_empty_twap_samples_returns_none(self):
        self.assertIsNone(_calculate_twap_from_samples([]))

    def test_single_sample_returns_none(self):
        samples = [PriceSnapshot(timestamp=1.0, block_number=1, price=100.0)]
        self.assertIsNone(_calculate_twap_from_samples(samples))

    def test_two_samples_returns_none(self):
        samples = [
            PriceSnapshot(timestamp=1.0, block_number=1, price=100.0),
            PriceSnapshot(timestamp=2.0, block_number=2, price=101.0),
        ]
        self.assertIsNone(_calculate_twap_from_samples(samples))

    def test_all_samples_same_price(self):
        samples = [PriceSnapshot(timestamp=float(i), block_number=i, price=100.0) for i in range(10)]
        window = _calculate_twap_from_samples(samples)
        self.assertIsNotNone(window)
        self.assertEqual(window.std_dev_pct, 0.0)

    def test_extreme_price_spike(self):
        samples = [PriceSnapshot(timestamp=float(i), block_number=i, price=100.0) for i in range(9)]
        samples.append(PriceSnapshot(timestamp=10.0, block_number=10, price=10000.0))  # 100x spike
        window = _calculate_twap_from_samples(samples)
        self.assertIsNotNone(window)
        self.assertGreater(window.manipulation_risk(), 0.5)

    def test_price_near_zero(self):
        read = OracleRead(
            tx_hash="0xabc",
            block_number=1,
            timestamp=1.0,
            oracle_address="0xfeed",
            oracle_type=OracleType.CHAINLINK,
            reported_price=0.001,
            expected_price=0.001,
        )
        dev = read.deviation_from_expected()
        self.assertIsNotNone(dev)
        self.assertAlmostEqual(dev, 0.0)

    def test_classify_severity_extreme(self):
        self.assertEqual(_classify_severity(1.0), Severity.CRITICAL)
        self.assertEqual(_classify_severity(0.5), Severity.CRITICAL)

    def test_sandwich_pattern_small_window(self):
        prices = [100.0, 200.0, 100.0]
        self.assertTrue(_is_sandwich_pattern(prices, window=1))

    def test_no_sandwich_missing_recovery(self):
        prices = [100.0, 100.0, 100.0, 200.0, 200.0, 200.0]
        self.assertFalse(_is_sandwich_pattern(prices, window=2))


if __name__ == "__main__":
    unittest.main()
