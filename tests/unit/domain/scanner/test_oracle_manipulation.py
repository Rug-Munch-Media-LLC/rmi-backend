"""Unit tests for app/domains/scanners/oracle_manipulation.py.

Tests price deviation detection, TWAP / thin-pool manipulation detection,
Chainlink usage checks, manipulable-score computation, risk calculation,
and the full analyze() pipeline with mocked external price feeds
(DexScreener, Birdeye, RPC). No network access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanners.oracle_manipulation import (
    CHAINLINK_FEED_REGISTRY,
    OracleManipulationDetector,
    OracleManipulationReport,
)


def _pair(price_usd: str | float, liquidity_usd: float) -> dict:
    """Build a DexScreener-style pair dict."""
    return {"priceUsd": str(price_usd), "liquidity": {"usd": liquidity_usd}}


class TestPriceDeviation:
    """DEX vs oracle price deviation detection."""

    @pytest.mark.asyncio
    async def test_large_deviation_detected(self) -> None:
        """A 30% DEX vs Birdeye deviation should be flagged as critical."""
        detector = OracleManipulationDetector()
        pair = _pair(1.00, 500_000)
        with (
            patch.object(detector, "_fetch_dexscreener_token", AsyncMock(return_value=pair)),
            patch.object(detector, "_fetch_birdeye_price", AsyncMock(return_value=1.30)),
            patch.object(detector, "_check_chainlink_usage", AsyncMock(return_value=(False, None))),
            patch.object(detector, "_fetch_contract_source", AsyncMock(return_value=None)),
            patch("rugmunch_scanner.detectors.oracle_manipulation.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("0xtoken", "ethereum")
        assert report.price_deviation_pct == pytest.approx(30.0)
        assert any("Price deviation 30.0%" in w for w in report.warnings)
        assert report.risk_level == "CRITICAL"

    @pytest.mark.asyncio
    async def test_small_deviation_within_threshold(self) -> None:
        """Deviations under 5% should not produce a deviation warning."""
        detector = OracleManipulationDetector()
        pair = _pair(1.00, 500_000)
        with (
            patch.object(detector, "_fetch_dexscreener_token", AsyncMock(return_value=pair)),
            patch.object(detector, "_fetch_birdeye_price", AsyncMock(return_value=1.02)),
            patch.object(detector, "_check_chainlink_usage", AsyncMock(return_value=(False, None))),
            patch.object(detector, "_fetch_contract_source", AsyncMock(return_value=None)),
            patch("rugmunch_scanner.detectors.oracle_manipulation.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("0xtoken", "ethereum")
        assert report.price_deviation_pct == pytest.approx(2.0)
        assert not any("Price deviation" in w for w in report.warnings)

    @pytest.mark.asyncio
    async def test_no_deviation_without_second_price(self) -> None:
        """Missing Birdeye price should leave deviation at 0.0."""
        detector = OracleManipulationDetector()
        pair = _pair(1.00, 500_000)
        with (
            patch.object(detector, "_fetch_dexscreener_token", AsyncMock(return_value=pair)),
            patch.object(detector, "_fetch_birdeye_price", AsyncMock(return_value=None)),
            patch.object(detector, "_check_chainlink_usage", AsyncMock(return_value=(False, None))),
            patch.object(detector, "_fetch_contract_source", AsyncMock(return_value=None)),
            patch("rugmunch_scanner.detectors.oracle_manipulation.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("0xtoken", "ethereum")
        assert report.price_deviation_pct == 0.0


class TestTWAPManipulation:
    """Thin-pool / DEX-TWAP manipulation detection."""

    @pytest.mark.asyncio
    async def test_solana_thin_pool_flagged_twap(self) -> None:
        """Solana tokens in a <$100k pool should be single-source TWAP."""
        detector = OracleManipulationDetector()
        pair = _pair(0.01, 5_000)
        with (
            patch.object(detector, "_fetch_dexscreener_token", AsyncMock(return_value=pair)),
            patch.object(detector, "_fetch_birdeye_price", AsyncMock(return_value=0.011)),
            patch("rugmunch_scanner.detectors.oracle_manipulation.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("SolMint1111111111111111111111111111111", "solana")
        assert report.oracle_type == "uniswap_twap"
        assert report.is_single_source is True
        assert report.pool_depth_usd == 5_000
        assert report.manipulable_score >= 70
        assert report.risk_level == "CRITICAL"

    @pytest.mark.asyncio
    async def test_deep_pool_lower_manipulability(self) -> None:
        """Deep pools should score lower on manipulability."""
        detector = OracleManipulationDetector()
        pair = _pair(0.01, 2_000_000)
        with (
            patch.object(detector, "_fetch_dexscreener_token", AsyncMock(return_value=pair)),
            patch.object(detector, "_fetch_birdeye_price", AsyncMock(return_value=0.010)),
            patch("rugmunch_scanner.detectors.oracle_manipulation.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("SolMint1111111111111111111111111111111", "solana")
        assert report.pool_depth_usd == 2_000_000
        assert report.manipulable_score < 70


class TestComputeManipulableScore:
    """Pure manipulability scoring."""

    def test_critical_liquidity_scores_high(self) -> None:
        """Pools under $10k should score 50 for depth alone."""
        detector = OracleManipulationDetector()
        assert detector._compute_manipulable_score(5_000, True, 0.0) == pytest.approx(75.0)

    def test_deep_pool_scores_zero_for_depth(self) -> None:
        """Pools over $500k should score 0 for depth."""
        detector = OracleManipulationDetector()
        assert detector._compute_manipulable_score(1_000_000, False, 0.0) == pytest.approx(0.0)

    def test_single_source_adds_25(self) -> None:
        """Single-source oracles add a flat 25 to the score."""
        detector = OracleManipulationDetector()
        multi = detector._compute_manipulable_score(1_000_000, False, 0.0)
        single = detector._compute_manipulable_score(1_000_000, True, 0.0)
        assert single == multi + 25

    def test_critical_deviation_adds_25(self) -> None:
        """Deviations >= 20% add the full deviation factor."""
        detector = OracleManipulationDetector()
        base = detector._compute_manipulable_score(1_000_000, False, 0.0)
        dev = detector._compute_manipulable_score(1_000_000, False, 30.0)
        assert dev == base + 25

    def test_linear_scaling_between_thresholds(self) -> None:
        """Deviations between 5% and 20% scale linearly."""
        detector = OracleManipulationDetector()
        at_threshold = detector._compute_manipulable_score(1_000_000, False, 5.0)
        mid = detector._compute_manipulable_score(1_000_000, False, 12.5)
        assert at_threshold == 0.0
        assert mid == pytest.approx(7.5)


class TestCalculateRisk:
    """Risk scoring from oracle manipulation indicators."""

    def test_high_deviation_adds_critical_warning(self) -> None:
        """Critical price deviations drive the report to CRITICAL."""
        detector = OracleManipulationDetector()
        report = OracleManipulationReport(
            token_address="0x",
            chain="ethereum",
            oracle_type="custom",
            is_single_source=True,
            price_deviation_pct=25.0,
            manipulable_score=80.0,
            pool_depth_usd=5_000,
        )
        score, level, warnings = detector._calculate_risk(report)
        assert score >= 70
        assert level == "CRITICAL"
        assert any("possible oracle attack" in w for w in warnings)

    def test_clean_report_is_low_risk(self) -> None:
        """No indicators should yield a LOW risk report."""
        detector = OracleManipulationDetector()
        report = OracleManipulationReport(
            token_address="0x",
            chain="ethereum",
            oracle_type="chainlink",
            is_single_source=False,
            price_deviation_pct=0.0,
            manipulable_score=0.0,
            pool_depth_usd=5_000_000,
            known_oracle_used=True,
        )
        score, level, _ = detector._calculate_risk(report)
        assert score == 0
        assert level == "LOW"


class TestChainlinkUsage:
    """Chainlink oracle usage detection."""

    @pytest.mark.asyncio
    async def test_chainlink_feed_registry_eth_call(self) -> None:
        """A successful feed-registry eth_call should confirm Chainlink usage."""
        detector = OracleManipulationDetector()
        registry = CHAINLINK_FEED_REGISTRY["ethereum"]
        with patch.object(detector, "_eth_call", AsyncMock(return_value="0x" + "00" * 32)):
            uses, addr = await detector._check_chainlink_usage("0xtoken", "ethereum")
        assert uses is True
        assert addr == registry

    @pytest.mark.asyncio
    async def test_chainlink_detected_in_source_code(self) -> None:
        """Source code referencing AggregatorV3Interface should detect Chainlink."""
        detector = OracleManipulationDetector()
        with (
            patch.object(detector, "_eth_call", AsyncMock(return_value=None)),
            patch.object(
                detector,
                "_fetch_contract_source",
                AsyncMock(return_value="contract X { AggregatorV3Interface internal priceFeed; }"),
            ),
        ):
            uses, addr = await detector._check_chainlink_usage("0xtoken", "ethereum")
        assert uses is True
        assert addr == CHAINLINK_FEED_REGISTRY["ethereum"]

    @pytest.mark.asyncio
    async def test_no_chainlink_detection(self) -> None:
        """No registry hit and no source reference means no Chainlink."""
        detector = OracleManipulationDetector()
        with (
            patch.object(detector, "_eth_call", AsyncMock(return_value=None)),
            patch.object(detector, "_fetch_contract_source", AsyncMock(return_value=None)),
        ):
            uses, addr = await detector._check_chainlink_usage("0xtoken", "ethereum")
        assert uses is False
        assert addr is None

    @pytest.mark.asyncio
    async def test_chainlink_skipped_for_solana(self) -> None:
        """Chainlink checks should not run on non-EVM chains."""
        detector = OracleManipulationDetector()
        with patch.object(detector, "_eth_call", AsyncMock()) as mocked:
            uses, addr = await detector._check_chainlink_usage("SolMint", "solana")
        mocked.assert_not_awaited()
        assert uses is False
        assert addr is None


class TestAnalyzeEVM:
    """EVM analyze() oracle type detection."""

    @pytest.mark.asyncio
    async def test_analyze_custom_oracle_from_source(self) -> None:
        """Source mentioning 'oracle' should classify the oracle as custom."""
        detector = OracleManipulationDetector()
        pair = _pair(1.00, 500_000)
        with (
            patch.object(detector, "_fetch_dexscreener_token", AsyncMock(return_value=pair)),
            patch.object(detector, "_fetch_birdeye_price", AsyncMock(return_value=1.00)),
            patch.object(detector, "_check_chainlink_usage", AsyncMock(return_value=(False, None))),
            patch.object(
                detector,
                "_fetch_contract_source",
                AsyncMock(return_value="uses custom priceFeed oracle"),
            ),
            patch("rugmunch_scanner.detectors.oracle_manipulation.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("0xtoken", "ethereum")
        assert report.oracle_type == "custom"
        assert report.known_oracle_used is False

    @pytest.mark.asyncio
    async def test_analyze_chainlink_used(self) -> None:
        """Chainlink usage should set known_oracle_used on the report."""
        detector = OracleManipulationDetector()
        pair = _pair(1.00, 500_000)
        with (
            patch.object(detector, "_fetch_dexscreener_token", AsyncMock(return_value=pair)),
            patch.object(detector, "_fetch_birdeye_price", AsyncMock(return_value=1.00)),
            patch.object(
                detector,
                "_check_chainlink_usage",
                AsyncMock(return_value=(True, CHAINLINK_FEED_REGISTRY["ethereum"])),
            ),
            patch("rugmunch_scanner.detectors.oracle_manipulation.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("0xtoken", "ethereum")
        assert report.oracle_type == "chainlink"
        assert report.known_oracle_used is True
        assert CHAINLINK_FEED_REGISTRY["ethereum"] in report.oracle_addresses

    @pytest.mark.asyncio
    async def test_dexscreener_unavailable_graceful(self) -> None:
        """Missing DexScreener data should not crash the analysis."""
        detector = OracleManipulationDetector()
        with (
            patch.object(detector, "_fetch_dexscreener_token", AsyncMock(return_value=None)),
            patch.object(detector, "_fetch_birdeye_price", AsyncMock(return_value=None)),
            patch.object(detector, "_check_chainlink_usage", AsyncMock(return_value=(False, None))),
            patch.object(detector, "_fetch_contract_source", AsyncMock(return_value=None)),
            patch("rugmunch_scanner.detectors.oracle_manipulation.query_rag_citations", AsyncMock(return_value=[])),
        ):
            report = await detector.analyze("0xtoken", "ethereum")
        assert isinstance(report, OracleManipulationReport)
        assert report.pool_depth_usd == 0.0


if __name__ == "__main__":
    pytest.main([__file__])
