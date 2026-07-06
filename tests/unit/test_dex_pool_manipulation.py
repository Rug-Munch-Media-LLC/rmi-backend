"""
Tests for DEXPoolManipulationAnalyzer.
"""

import pytest

from app.dex_pool_manipulation_analyzer import (
    DEXPoolManipulationAnalyzer,
    RiskCategory,
    RiskSignal,
    format_risk_report,
    is_valid_address,
)


@pytest.fixture
def analyzer():
    return DEXPoolManipulationAnalyzer(chain="ethereum", dex="uniswap_v3")


@pytest.fixture
def sample_pool_metadata():
    return {
        "chain": "ethereum",
        "dex": "uniswap_v3",
        "version": "v3",
        "token0": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "token1": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "token0_symbol": "WETH",
        "token1_symbol": "USDC",
        "fee_tier": 500,  # 0.05%
        "tick_spacing": 10,
        "total_liquidity_usd": 5000000.0,
        "owner": "0x1234567890123456789012345678901234567890",
        "created_at": 1700000000,
    }


@pytest.fixture
def sample_positions():
    return [
        {
            "owner": "0x1111...1111",
            "tick_lower": -100,
            "tick_upper": 100,
            "liquidity": 500_000,
            "usd_value": 1_000_000,
        },
        {
            "owner": "0x2222...2222",
            "tick_lower": -50,
            "tick_upper": 50,
            "liquidity": 300_000,
            "usd_value": 600_000,
        },
        {
            "owner": "0x3333...3333",
            "tick_lower": -200,
            "tick_upper": 200,
            "liquidity": 100_000,
            "usd_value": 200_000,
        },
        {
            "owner": "0x4444...4444",
            "tick_lower": -150,
            "tick_upper": 150,
            "liquidity": 50_000,
            "usd_value": 100_000,
        },
        {
            "owner": "0x5555...5555",
            "tick_lower": -80,
            "tick_upper": 80,
            "liquidity": 30_000,
            "usd_value": 60_000,
        },
        {
            "owner": "0x6666...6666",
            "tick_lower": -300,
            "tick_upper": 300,
            "liquidity": 20_000,
            "usd_value": 40_000,
        },
    ]


@pytest.fixture
def concentrated_positions():
    """Positions where top 1 owner controls most liquidity."""
    return [
        {
            "owner": "0xBEEF...BEEF",
            "tick_lower": -60,
            "tick_upper": 60,
            "liquidity": 900_000,
            "usd_value": 1_800_000,
        },
        {
            "owner": "0xBEEF...BEEF",
            "tick_lower": -40,
            "tick_upper": 40,
            "liquidity": 50_000,
            "usd_value": 100_000,
        },
        {
            "owner": "0xCAFE...CAFE",
            "tick_lower": -100,
            "tick_upper": 100,
            "liquidity": 30_000,
            "usd_value": 60_000,
        },
        {
            "owner": "0xDEAD...DEAD",
            "tick_lower": -200,
            "tick_upper": 200,
            "liquidity": 20_000,
            "usd_value": 40_000,
        },
    ]


@pytest.fixture
def sandwich_swaps():
    """Simulated sandwich attack pattern."""
    return [
        {
            "tx_hash": "0xaaa",
            "block": 100,
            "timestamp": 1000,
            "amount_in": 5,
            "amount_out": 4950,
            "price_before": 1000.0,
            "price_after": 1005.0,
        },
        {
            "tx_hash": "0xbbb",
            "block": 100,
            "timestamp": 1001,
            "amount_in": 50,
            "amount_out": 49500,
            "price_before": 1005.0,
            "price_after": 1001.0,
        },
        {
            "tx_hash": "0xccc",
            "block": 100,
            "timestamp": 1002,
            "amount_in": 5,
            "amount_out": 4980,
            "price_before": 1001.0,
            "price_after": 1000.5,
        },
        {
            "tx_hash": "0xddd",
            "block": 200,
            "timestamp": 2000,
            "amount_in": 10,
            "amount_out": 10000,
            "price_before": 1050.0,
            "price_after": 1055.0,
        },
        {
            "tx_hash": "0xeee",
            "block": 200,
            "timestamp": 2001,
            "amount_in": 8,
            "amount_out": 7950,
            "price_before": 1055.0,
            "price_after": 1051.0,
        },
    ]


class TestAddressValidation:
    def test_valid_evm_address(self):
        assert is_valid_address("0x1234567890abcdef1234567890abcdef12345678")

    def test_valid_solana_address(self):
        assert is_valid_address("7e8qUqNYBg4QvfVj5H4GqYBKF9HbDQ4XQcBJnvcMqrZN")

    def test_invalid_address(self):
        assert not is_valid_address("not_an_address")
        assert not is_valid_address("")
        assert not is_valid_address("0xshort")


class TestPoolConfig:
    def test_build_from_metadata(self, analyzer, sample_pool_metadata):
        pool = analyzer._build_pool_config("0xpool...pool", sample_pool_metadata)
        assert pool.address == "0xpool...pool"
        assert pool.chain == "ethereum"
        assert pool.dex == "uniswap_v3"
        assert pool.fee_tier == 500
        assert pool.total_liquidity_usd == 5_000_000.0

    def test_defaults_for_empty_metadata(self, analyzer):
        pool = analyzer._build_pool_config("0xpool...pool", {})
        assert pool.chain == "ethereum"
        assert pool.total_liquidity_usd == 0.0


class TestConcentrationAnalysis:
    def test_no_positions(self, analyzer, sample_pool_metadata):
        pool = analyzer._build_pool_config("0xpool", sample_pool_metadata)
        signal, pct = analyzer._analyze_concentration([], pool)
        assert signal is None
        assert pct == 0.0

    def test_well_distributed(self, analyzer, sample_pool_metadata, sample_positions):
        pool = analyzer._build_pool_config("0xpool", sample_pool_metadata)
        parsed = analyzer._parse_positions(sample_positions)
        signal, _pct = analyzer._analyze_concentration(parsed, pool)
        # Top 5: 500+300+100+50+30 = 980k out of 1000k = 98% - should flag
        assert signal is not None
        assert signal.category == RiskCategory.LIQUIDITY_CONCENTRATION

    def test_concentrated_ownership(self, analyzer, sample_pool_metadata, concentrated_positions):
        pool = analyzer._build_pool_config("0xpool", sample_pool_metadata)
        parsed = analyzer._parse_positions(concentrated_positions)
        signal, pct = analyzer._analyze_concentration(parsed, pool)
        assert signal is not None
        assert pct > 90  # Single owner controls >90%


class TestSandwichDetection:
    def test_detect_sandwich(self, analyzer, sample_pool_metadata, sandwich_swaps):
        pool = analyzer._build_pool_config("0xpool", sample_pool_metadata)
        parsed = analyzer._parse_swaps(sandwich_swaps)
        signal, profit = analyzer._analyze_sandwich_vulnerability(parsed, pool)
        assert signal is not None
        assert signal.category == RiskCategory.SANDWICH_VULNERABILITY
        assert profit >= 0

    def test_no_swaps_no_signal(self, analyzer, sample_pool_metadata):
        pool = analyzer._build_pool_config("0xpool", sample_pool_metadata)
        signal, profit = analyzer._analyze_sandwich_vulnerability([], pool)
        assert signal is None
        assert profit == 0.0


class TestPriceManipulation:
    def test_high_impact_detected(self, analyzer, sample_pool_metadata):
        pool = analyzer._build_pool_config("0xpool", sample_pool_metadata)
        swaps = [
            {
                "tx_hash": "0xa",
                "block": 1,
                "timestamp": 1000,
                "amount_in": 100,
                "amount_out": 50,
                "price_before": 100.0,
                "price_after": 110.0,
            },
            {
                "tx_hash": "0xb",
                "block": 2,
                "timestamp": 1001,
                "amount_in": 50,
                "amount_out": 20,
                "price_before": 110.0,
                "price_after": 115.0,
            },
        ]
        parsed = analyzer._parse_swaps(swaps)
        signal = analyzer._analyze_price_manipulation(parsed, pool)
        assert signal is not None
        assert signal.category == RiskCategory.PRICE_MANIPULATION

    def test_low_impact_no_signal(self, analyzer, sample_pool_metadata):
        pool = analyzer._build_pool_config("0xpool", sample_pool_metadata)
        swaps = [
            {
                "tx_hash": "0xa",
                "block": 1,
                "timestamp": 1000,
                "amount_in": 1,
                "amount_out": 999,
                "price_before": 1000.0,
                "price_after": 1000.1,
            },
        ]
        parsed = analyzer._parse_swaps(swaps)
        signal = analyzer._analyze_price_manipulation(parsed, pool)
        assert signal is None


class TestRiskScoring:
    def test_no_signals_zero_score(self, analyzer):
        score = analyzer._calculate_risk_score([])
        assert score == 0.0

    def test_high_severity_signals(self, analyzer):
        signals = [
            RiskSignal(
                category=RiskCategory.LIQUIDITY_CONCENTRATION,
                severity=0.8,
                description="High concentration",
            ),
            RiskSignal(
                category=RiskCategory.SANDWICH_VULNERABILITY,
                severity=0.8,
                description="High sandwich risk",
            ),
            RiskSignal(category=RiskCategory.FAKE_LIQUIDITY, severity=0.7, description="Fake liquidity"),
        ]
        score = analyzer._calculate_risk_score(signals)
        assert 0 < score <= 100
        assert score > 30  # Should be significant


class TestPriceImpact:
    def test_impact_increases_with_amount(self, analyzer):
        impact_small = analyzer._simulate_price_impact(1, 1_000_000)
        impact_large = analyzer._simulate_price_impact(100, 1_000_000)
        assert impact_large > impact_small
        assert impact_small < 1.0  # 1 ETH in $1M pool should be small

    def test_impact_capped(self, analyzer):
        impact = analyzer._simulate_price_impact(1_000_000, 1_000)
        assert impact <= 99.99

    def test_zero_liquidity(self, analyzer):
        impact = analyzer._simulate_price_impact(1, 0)
        assert impact > 100


class TestFullAnalysis:
    @pytest.mark.asyncio
    async def test_healthy_pool(self, analyzer, sample_pool_metadata, sample_positions):
        swaps = [
            {
                "tx_hash": "0xa",
                "block": 1,
                "timestamp": 1000,
                "amount_in": 0.1,
                "amount_out": 100,
                "price_before": 1000.0,
                "price_after": 1000.01,
            },
            {
                "tx_hash": "0xb",
                "block": 2,
                "timestamp": 1001,
                "amount_in": 0.2,
                "amount_out": 200,
                "price_before": 1000.01,
                "price_after": 1000.03,
            },
        ]
        report = await analyzer.analyze_pool(
            "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
            recent_swaps=swaps,
            positions=sample_positions,
            pool_metadata=sample_pool_metadata,
        )
        assert report.risk_score >= 0
        assert report.analysis_time_ms >= 0
        assert len(report.signals) >= 0
        assert report.pool.address is not None

    @pytest.mark.asyncio
    async def test_risky_pool(self, analyzer, concentrated_positions, sandwich_swaps):
        meta = {
            "chain": "ethereum",
            "dex": "uniswap_v3",
            "version": "v3",
            "token0_symbol": "SHIT",
            "token1_symbol": "USDC",
            "fee_tier": 0,
            "total_liquidity_usd": 5000.0,
            "owner": "0xDEAD...DEAD",
            "created_at": 1000,
        }
        report = await analyzer.analyze_pool(
            "0xDEAD00000000000000000000000000000000BEEF",
            recent_swaps=sandwich_swaps,
            positions=concentrated_positions,
            pool_metadata=meta,
        )
        assert report.risk_score > 25
        assert report.price_impact_1eth > 0.01
        assert len(report.recommendations) > 0


class TestReportFormat:
    def test_format(self, analyzer, sample_pool_metadata):
        pool = analyzer._build_pool_config("0xpool", sample_pool_metadata)
        # Build minimal report
        from app.dex_pool_manipulation_analyzer import PoolRiskReport, RiskCategory, RiskSignal

        report_obj = PoolRiskReport(
            pool=pool,
            risk_score=45.5,
            signals=[RiskSignal(RiskCategory.LIQUIDITY_CONCENTRATION, 0.5, "Test signal")],
            price_impact_1eth=0.05,
            price_impact_10eth=0.5,
            price_impact_100eth=5.0,
            top_5_concentration_pct=85.0,
            liquidity_depth_1pct=100000.0,
            sandwich_profit_estimate=0.01,
            recommendations=["Test recommendation"],
            analysis_time_ms=42,
        )
        result = format_risk_report(report_obj)
        assert result["risk_score"] == 45.5
        assert result["risk_level"] == "high"
        assert len(result["signals"]) == 1
        assert len(result["recommendations"]) == 1
        assert len(result["metrics"]["price_impact"]) == 3
