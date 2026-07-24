"""
Tests for Oracle Manipulation Detector
=======================================
Covers core detection heuristics: TWAP manipulation, Chainlink staleness,
flash loan-backed manipulation, cross-pool divergence, severity
classification, sandwich detection, and data model serialization -
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
    calculate_twap_from_samples,
    classify_severity,
    compute_deviation_pct,
    detect_twap_manipulation,
    is_sandwich_pattern,
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
        assert snap.price == 3200.5
        assert snap.block_number == 20000000
        assert snap.chain == "ethereum"

    def test_to_dict(self):
        snap = PriceSnapshot(
            timestamp=1234567890.0,
            block_number=20000000,
            price=3200.50,
        )
        d = snap.to_dict()
        assert d["price"] == 3200.5
        assert d["block_number"] == 20000000
        assert d["chain"] == ""  # default changed after split


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
            price_age_seconds=300,  # 5 min - fresh
        )
        assert not read.is_stale()

    def test_stale_feed(self):
        read = OracleRead(
            tx_hash="0xabc",
            block_number=20000000,
            timestamp=1234567890.0,
            oracle_address="0xdead",
            oracle_type=OracleType.CHAINLINK,
            reported_price=3200.0,
            expected_price=3200.0,
            price_age_seconds=86400,  # 24 hours - very stale
        )
        assert read.is_stale()

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
        assert deviation is not None
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
        assert read.deviation_from_expected() is None

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
        assert d["oracle_type"] == "chainlink"
        assert not d["is_stale"]
        # deviation_pct is computed dynamically in to_dict when expected_price is set
        self.assertAlmostEqual(d.get("deviation_pct", 0), 0.0, places=5)

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
        assert d["expected_price"] is None
        # deviation_pct is not included when no expected_price is set


class TestOracleType(unittest.TestCase):
    """OracleType enum tests."""

    def test_from_string(self):
        assert OracleType.from_string("chainlink") == OracleType.CHAINLINK
        assert OracleType.from_string("uniswap_v3_twap") == OracleType.UNISWAP_V3_TWAP
        assert OracleType.from_string("uniswap_v2_twap") == OracleType.UNISWAP_V2_TWAP
        assert OracleType.from_string("curve_ema") == OracleType.CURVE_EMA
        assert OracleType.from_string("unknown_foo") == OracleType.CUSTOM
        assert OracleType.from_string("") == OracleType.CUSTOM


class TestSeverity(unittest.TestCase):
    """Severity enum ordering and score tests."""

    def test_severity_ordering(self):
        assert Severity.INFO < Severity.LOW
        assert Severity.LOW < Severity.MEDIUM
        assert Severity.MEDIUM < Severity.HIGH
        assert Severity.HIGH < Severity.CRITICAL

    def test_severity_scores(self):
        assert Severity.CRITICAL.score == 1.0
        assert Severity.INFO.score == 0.0
        assert Severity.HIGH.score > Severity.MEDIUM.score


class TestManipulationType(unittest.TestCase):
    """ManipulationType label tests."""

    def test_label_format(self):
        mt = ManipulationType.TWAP_POISONING
        assert mt.value == "twap_poisoning"


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
        assert "HIGH" in s
        assert "TWAP" in s
        assert "WETH/USDC" in s
        assert "6.25" in s

    def test_label(self):
        label = self.incident.label()
        assert "HIGH" in label
        assert "twap_poisoning" in label

    def test_to_dict(self):
        d = self.incident.to_dict()
        assert d["manipulation_type"] == "twap_poisoning"
        assert d["severity"] == "high"
        assert d["deviation_pct"] == 6.25


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
            if i in (3, 4, 5):  # Spike in middle  # noqa: SIM108
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
        window = calculate_twap_from_samples(self.stable_samples)
        assert window is not None
        assert window.average_price == 3200.0
        assert window.min_price == 3200.0
        assert window.max_price == 3200.0
        assert window.std_dev_pct == 0.0
        # 0 std dev + 10 samples = 0 manipulation risk
        assert window.manipulation_risk() == 0.0

    def test_volatile_twap(self):
        window = calculate_twap_from_samples(self.volatile_samples)
        assert window is not None
        assert window.average_price > 3200.0
        assert window.std_dev_pct > 0.01
        assert window.manipulation_risk() > 0.2

    def test_twap_manipulation_detection(self):
        # Compute TWAP from volatile samples
        window = calculate_twap_from_samples(self.volatile_samples)
        assert window is not None

        incident = detect_twap_manipulation(window)
        # High volatility should trigger detection
        assert incident is not None
        assert incident.manipulation_type == ManipulationType.TWAP_POISONING
        assert incident.severity in [
            Severity.CRITICAL,
            Severity.HIGH,
            Severity.MEDIUM,
            Severity.LOW,
        ]

    def test_insufficient_samples(self):
        samples = [PriceSnapshot(timestamp=1.0, block_number=1, price=100.0)]
        assert calculate_twap_from_samples(samples) is None

    def test_twap_to_dict(self):
        window = calculate_twap_from_samples(self.stable_samples)
        d = window.to_dict()
        assert "average_price" in d
        assert "manipulation_risk" in d
        assert "samples" in d
        assert isinstance(d["samples"], list)
        assert len(d["samples"]) == 10


class TestHeuristicHelpers(unittest.TestCase):
    """Test standalone heuristic functions."""

    def testcompute_deviation_pct(self):
        self.assertAlmostEqual(compute_deviation_pct(3200.0, 3200.0), 0.0)
        self.assertAlmostEqual(compute_deviation_pct(3400.0, 3200.0), 0.0625, places=4)
        self.assertAlmostEqual(compute_deviation_pct(3000.0, 3200.0), 0.0625, places=4)

    def test_compute_deviation_zero(self):
        assert compute_deviation_pct(100.0, 0.0) == 0.0

    def testclassify_severity_normal(self):
        assert classify_severity(0.01) == Severity.INFO
        assert classify_severity(0.04) == Severity.LOW
        assert classify_severity(0.09) == Severity.MEDIUM
        assert classify_severity(0.2) == Severity.HIGH
        assert classify_severity(0.5) == Severity.CRITICAL

    def testclassify_severity_flash_loan(self):
        # Flash loan thresholds are tighter
        assert classify_severity(0.04, is_flash_loan=True) == Severity.INFO
        assert classify_severity(0.06, is_flash_loan=True) == Severity.MEDIUM
        assert classify_severity(0.12, is_flash_loan=True) == Severity.HIGH
        assert classify_severity(0.25, is_flash_loan=True) == Severity.CRITICAL

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
        assert is_sandwich_pattern(prices, window=3)

    def test_no_sandwich_pattern(self):
        # Flat prices
        prices = [100.0] * 15
        assert not is_sandwich_pattern(prices)

    def test_sandwich_too_few_samples(self):
        assert not is_sandwich_pattern([100.0, 100.0], window=5)


class TestOracleManipulationDetector(unittest.TestCase):
    """Integration tests for OracleManipulationDetector."""

    def setUp(self):
        self.detector = OracleManipulationDetector(chain="ethereum")

    def test_scan_produces_report(self):
        """Full scan should produce a valid report structure."""
        import asyncio

        report = asyncio.run(self.detector.scan(blocks_back=30))
        assert report is not None
        assert report.blocks_scanned > 0
        assert len(report.scan_id) > 0

    def test_scan_checks_pools(self):
        """Scan should check some LP pools."""
        import asyncio

        report = asyncio.run(self.detector.scan(blocks_back=30))
        assert len(report.twap_windows) > 0
        assert len(report.incidents) + len(report.errors) == report.total_incidents + len(
            report.errors
        )

    def test_pool_analysis(self):
        """Pool analysis should produce a TWAP window."""
        import asyncio

        report = asyncio.run(
            self.detector.analyze_pool(
                "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
                "WETH/USDC",
            )
        )
        assert report is not None
        assert report.pools_checked == 1
        if report.twap_windows:
            twap = report.twap_windows[0]
            assert twap.average_price > 0

    def test_chainlink_feed_check(self):
        """Chainlink feed check should return feeds."""
        import asyncio

        reads = asyncio.run(self.detector.check_chainlink_feeds(["ETH/USD", "BTC/USD"]))
        assert len(reads) > 0
        for read in reads:
            assert read.oracle_type == OracleType.CHAINLINK
            assert read.reported_price > 0

    def test_multichain_scan(self):
        """Multi-chain scan should work."""
        import asyncio

        report = asyncio.run(
            self.detector.scan(
                blocks_back=10,
                chains=["ethereum", "base"],
            )
        )
        assert report is not None
        assert "ethereum" in report.chain
        assert "base" in report.chain

    def test_report_json_serialization(self):
        """Report should serialize to valid JSON."""
        import asyncio

        report = asyncio.run(self.detector.scan(blocks_back=10))
        json_str = report.json()
        data = json.loads(json_str)
        assert "scan_id" in data
        assert "incidents" in data
        assert "twap_windows" in data


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
        assert report.risk_score == 0.0

        report.incidents.append(
            PriceManipulation(
                manipulation_type=ManipulationType.TWAP_POISONING,
                severity=Severity.CRITICAL,
                chain="ethereum",
                block_number=1,
                pool_address="0xpool",
                pair="WETH/USDC",
                oracle_type=OracleType.UNISWAP_V3_TWAP,
                observed_price=3400.0,
                expected_price=3200.0,
                deviation_pct=6.25,
            )
        )
        # risk_score = severity.score (1.0) * (1 + deviation_pct/100) / count
        # = 1.0 * (1 + 6.25/100) / 1 = 1.0625
        assert report.risk_score > 0

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
                pool_address="0xpool1",
                pair="WETH/USDC",
                oracle_type=OracleType.UNISWAP_V3_TWAP,
                observed_price=3400.0,
                expected_price=3200.0,
                deviation_pct=6.25,
            )
        )
        report.incidents.append(
            PriceManipulation(
                manipulation_type=ManipulationType.CHAINLINK_STALE,
                severity=Severity.HIGH,
                chain="ethereum",
                block_number=2,
                pool_address="0xpool2",
                pair="ETH/USD",
                oracle_type=OracleType.CHAINLINK,
                observed_price=3200.0,
                expected_price=3200.0,
                deviation_pct=0.0,
            )
        )
        report.incidents.append(
            PriceManipulation(
                manipulation_type=ManipulationType.LP_PRICE_DIVERGENCE,
                severity=Severity.LOW,
                chain="ethereum",
                block_number=3,
                pool_address="0xpool3",
                pair="WBTC/USDC",
                oracle_type=OracleType.UNISWAP_V3_TWAP,
                observed_price=60000.0,
                expected_price=60000.0,
                deviation_pct=0.0,
            )
        )

        assert report.critical_count == 1
        assert report.high_count == 1
        assert report.total_incidents == 3

    def test_duration(self):
        from app.oracle_manipulation_detector import ManipulationReport

        report = ManipulationReport(
            scan_id="test",
            chain="ethereum",
            blocks_scanned=50,
            start_time=100.0,
            end_time=150.0,
        )
        assert report.duration_seconds == 50.0


class TestKnownConstants(unittest.TestCase):
    """Test that KNOWN_LP_POOLS and CHAINLINK_FEEDS are well-formed."""

    def test_known_lp_pools_have_expected_structure(self):
        for _pair, pools in KNOWN_LP_POOLS.items():
            for pool in pools:
                assert "protocol" in pool
                assert "address" in pool
                assert pool["protocol"] in ["Uniswap V3", "Uniswap V2", "Curve", "Balancer"]

    def test_chainlink_feeds_have_required_fields(self):
        for _name, feed in CHAINLINK_FEEDS.items():
            assert "address" in feed
            assert "decimals" in feed
            assert "heartbeat" in feed
            assert feed["decimals"] > 0

    def test_MIN_TWAP_SAMPLES_reasonable(self):
        assert MIN_TWAP_SAMPLES >= 2
        assert MIN_TWAP_SAMPLES <= 10

    def test_cross_pool_divergence_threshold_positive(self):
        assert CROSS_POOL_DIVERGENCE_THRESHOLD > 0
        assert CROSS_POOL_DIVERGENCE_THRESHOLD < 0.5

    def test_twap_manipulation_threshold_positive(self):
        assert TWAP_MANIPULATION_THRESHOLD_PCT > 0
        assert TWAP_MANIPULATION_THRESHOLD_PCT < 0.5


class TestEdgeCases(unittest.TestCase):
    """Edge case and boundary tests."""

    def test_empty_twap_samples_returns_none(self):
        assert calculate_twap_from_samples([]) is None

    def test_single_sample_returns_none(self):
        samples = [PriceSnapshot(timestamp=1.0, block_number=1, price=100.0)]
        assert calculate_twap_from_samples(samples) is None

    def test_two_samples_returns_none(self):
        samples = [
            PriceSnapshot(timestamp=1.0, block_number=1, price=100.0),
            PriceSnapshot(timestamp=2.0, block_number=2, price=101.0),
        ]
        assert calculate_twap_from_samples(samples) is None

    def test_all_samples_same_price(self):
        samples = [
            PriceSnapshot(timestamp=float(i), block_number=i, price=100.0) for i in range(10)
        ]
        window = calculate_twap_from_samples(samples)
        assert window is not None
        assert window.std_dev_pct == 0.0

    def test_extreme_price_spike(self):
        samples = [PriceSnapshot(timestamp=float(i), block_number=i, price=100.0) for i in range(9)]
        samples.append(PriceSnapshot(timestamp=10.0, block_number=10, price=10000.0))  # 100x spike
        window = calculate_twap_from_samples(samples)
        assert window is not None
        assert window.manipulation_risk() > 0.5

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
        assert dev is not None
        self.assertAlmostEqual(dev, 0.0)

    def test_classify_severity_extreme(self):
        assert classify_severity(1.0) == Severity.CRITICAL
        assert classify_severity(0.5) == Severity.CRITICAL

    def test_sandwich_pattern_small_window(self):
        prices = [100.0, 200.0, 100.0]
        assert is_sandwich_pattern(prices, window=1)

    def test_no_sandwich_missing_recovery(self):
        prices = [100.0, 100.0, 100.0, 200.0, 200.0, 200.0]
        assert not is_sandwich_pattern(prices, window=2)


if __name__ == "__main__":
    unittest.main()
