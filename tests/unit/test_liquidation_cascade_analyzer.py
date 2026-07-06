"""
Tests for Liquidation Cascade Risk Analyzer
============================================
Tests cover:
  - Address validation (EVM + Solana)
  - CollateralPosition creation and serialization
  - DebtPosition creation and serialization
  - ProtocolPosition health computation (all risk tiers)
  - LiquidationAnalysis pipeline (scenarios, clusters, reporting)
  - Edge cases: empty wallet, no debt, invalid addresses, missing Web3
"""

import json
import os
import sys

# Add app path so we can import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.liquidation_cascade_analyzer import (
    CascadeScenario,
    CollateralPosition,
    DebtPosition,
    LiquidationAnalysis,
    LiquidationCascadeAnalyzer,
    LiquidationCluster,
    ProtocolPosition,
    RiskTier,
    _estimate_asset_ltv,
    _estimate_asset_price,
    _resolve_asset_symbol,
)

# ══════════════════════════════════════════════════════════════════════════
# Address Validation
# ══════════════════════════════════════════════════════════════════════════


class TestAddressValidation:
    def setup_method(self):
        self.analyzer = LiquidationCascadeAnalyzer()

    def test_valid_evm_address(self):
        assert self.analyzer._validate_address("0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18")

    def test_valid_evm_address_lowercase(self):
        assert self.analyzer._validate_address("0x742d35cc6634c0532925a3b844bc9e7595f2bd18")

    def test_valid_solana_address(self):
        assert self.analyzer._validate_address("7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLrH")

    def test_invalid_address_too_short(self):
        assert not self.analyzer._validate_address("0x1234")

    def test_invalid_address_bad_prefix(self):
        assert not self.analyzer._validate_address("1x742d35Cc6634C0532925a3b844Bc9e7595f2bD18")

    def test_invalid_address_empty(self):
        assert not self.analyzer._validate_address("")

    def test_invalid_address_random_string(self):
        assert not self.analyzer._validate_address("not-an-address")


# ══════════════════════════════════════════════════════════════════════════
# CollateralPosition
# ══════════════════════════════════════════════════════════════════════════


class TestCollateralPosition:
    def test_create_basic(self):
        pos = CollateralPosition(
            asset="WETH",
            asset_address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
            amount_usd=50000.0,
            amount_token=17.857,
            ltv=0.80,
            liquidation_threshold=0.83,
            price_usd=2800.0,
        )
        assert pos.asset == "WETH"
        assert pos.amount_usd == 50000.0
        assert pos.amount_token == 17.857
        assert pos.ltv == 0.80
        assert pos.liquidation_threshold == 0.83

    def test_to_dict(self):
        pos = CollateralPosition(
            asset="USDC",
            asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            amount_usd=10000.0,
            amount_token=10000.0,
            ltv=0.80,
            liquidation_threshold=0.85,
            price_usd=1.0,
        )
        d = pos.to_dict()
        assert d["asset"] == "USDC"
        assert d["amount_usd"] == 10000.0
        assert d["liquidation_threshold"] == 0.85

    def test_zero_amount(self):
        pos = CollateralPosition(
            asset="ETH",
            asset_address="0x0000000000000000000000000000000000000000",
            amount_usd=0.0,
            amount_token=0.0,
            ltv=0.80,
            liquidation_threshold=0.83,
            price_usd=2800.0,
        )
        assert pos.amount_usd == 0.0
        assert pos.amount_token == 0.0


# ══════════════════════════════════════════════════════════════════════════
# DebtPosition
# ══════════════════════════════════════════════════════════════════════════


class TestDebtPosition:
    def test_create_basic(self):
        pos = DebtPosition(
            asset="USDC",
            asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            amount_usd=20000.0,
            amount_token=20000.0,
            variable_rate=5.0,
        )
        assert pos.asset == "USDC"
        assert pos.amount_usd == 20000.0
        assert pos.variable_rate == 5.0

    def test_with_stable_rate(self):
        pos = DebtPosition(
            asset="USDC",
            asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            amount_usd=10000.0,
            amount_token=10000.0,
            variable_rate=3.5,
            stable_rate=4.2,
        )
        assert pos.stable_rate == 4.2

    def test_to_dict(self):
        pos = DebtPosition(
            asset="DAI",
            asset_address="0x6b175474e89094c44da98b954eedeac495271d0f",
            amount_usd=5000.0,
            amount_token=5000.0,
            variable_rate=4.8,
        )
        d = pos.to_dict()
        assert d["asset"] == "DAI"
        assert d["variable_rate"] == 4.8


# ══════════════════════════════════════════════════════════════════════════
# ProtocolPosition - Health Computation
# ══════════════════════════════════════════════════════════════════════════


class TestProtocolPositionHealth:
    def make_position(
        self,
        coll_usd: float = 100000.0,
        debt_usd: float = 0.0,
        liq_threshold: float = 0.83,
        coll_asset: str = "WETH",
    ) -> ProtocolPosition:
        coll = CollateralPosition(
            asset=coll_asset,
            asset_address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
            amount_usd=coll_usd,
            amount_token=coll_usd / 2800.0,
            ltv=liq_threshold * 0.95,
            liquidation_threshold=liq_threshold,
            price_usd=2800.0,
        )
        debt = (
            DebtPosition(
                asset="USDC",
                asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                amount_usd=debt_usd,
                amount_token=debt_usd,
                variable_rate=5.0,
            )
            if debt_usd > 0
            else None
        )
        pos = ProtocolPosition(
            protocol="Aave V3",
            chain="ethereum",
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            collateral=[coll],
            debt=[debt] if debt else [],
            total_collateral_usd=coll_usd,
            total_debt_usd=debt_usd,
        )
        pos.compute_health()
        return pos

    def test_safe_no_debt(self):
        pos = self.make_position(debt_usd=0)
        assert pos.risk_tier == RiskTier.SAFE
        assert pos.health_factor is None or pos.health_factor == float("inf")

    def test_safe_low_debt(self):
        # $100k collateral, $20k debt, 83% threshold
        # HF = (100000 * 0.83) / 20000 = 4.15 (> 2.0 → SAFE)
        pos = self.make_position(debt_usd=20000.0)
        assert pos.risk_tier == RiskTier.SAFE
        assert pos.health_factor is not None
        assert pos.health_factor >= 2.0

    def test_watch_moderate_debt(self):
        # $100k collateral, $55k debt, 83% threshold
        # HF = (100000 * 0.83) / 55000 = 1.51 (> 1.5 → WATCH)
        pos = self.make_position(debt_usd=55000.0)
        assert pos.health_factor is not None
        assert pos.risk_tier == RiskTier.WATCH, f"Expected WATCH, got {pos.risk_tier} (HF={pos.health_factor})"
        assert 1.5 <= pos.health_factor < 2.0

    def test_danger_high_debt(self):
        # $100k collateral, $70k debt, 83% threshold
        # HF = (100000 * 0.83) / 70000 = 1.19 (> 1.1 → DANGER)
        pos = self.make_position(debt_usd=70000.0)
        assert pos.health_factor is not None
        assert pos.risk_tier == RiskTier.DANGER, f"Expected DANGER, got {pos.risk_tier} (HF={pos.health_factor})"
        assert 1.1 <= pos.health_factor < 1.5

    def test_critical_extreme_debt(self):
        # $100k collateral, $95k debt, 83% threshold
        # HF = (100000 * 0.83) / 95000 = 0.87 (< 1.1 → CRITICAL)
        pos = self.make_position(debt_usd=95000.0)
        assert pos.health_factor is not None
        assert pos.risk_tier == RiskTier.CRITICAL, f"Expected CRITICAL, got {pos.risk_tier} (HF={pos.health_factor})"
        assert 0 < pos.health_factor < 1.1

    def test_liquidation_price_computed(self):
        pos = self.make_position(debt_usd=50000.0, coll_usd=100000.0)
        assert pos.liquidation_price_usd is not None
        # 50000 / (100000/2800 * 0.83) = 50000 / (35.71 * 0.83) = 50000 / 29.64 = ~1686
        expected = 50000.0 / ((100000.0 / 2800.0) * 0.83)
        assert abs(pos.liquidation_price_usd - expected) < 1.0

    def test_multiple_collateral_weighted(self):
        """Test health factor with multiple collateral assets."""
        weth = CollateralPosition(
            asset="WETH",
            asset_address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
            amount_usd=60000.0,
            amount_token=21.43,
            ltv=0.76,
            liquidation_threshold=0.79,
            price_usd=2800.0,
        )
        usdc = CollateralPosition(
            asset="USDC",
            asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            amount_usd=40000.0,
            amount_token=40000.0,
            ltv=0.80,
            liquidation_threshold=0.85,
            price_usd=1.0,
        )
        debt = DebtPosition(
            asset="USDC",
            asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            amount_usd=50000.0,
            amount_token=50000.0,
            variable_rate=5.0,
        )
        pos = ProtocolPosition(
            protocol="Aave V3",
            chain="ethereum",
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            collateral=[weth, usdc],
            debt=[debt],
            total_collateral_usd=100000.0,
            total_debt_usd=50000.0,
        )
        pos.compute_health()
        # Weighted threshold: (60000*0.79 + 40000*0.85) / 100000 = (47400+34000)/100000 = 0.814
        # HF = (100000 * 0.814) / 50000 = 1.628
        assert pos.health_factor is not None
        assert pos.risk_tier == RiskTier.WATCH, f"Expected WATCH, got {pos.risk_tier} (HF={pos.health_factor})"
        assert 1.5 < pos.health_factor < 1.8


# ══════════════════════════════════════════════════════════════════════════
# LiquidationAnalysis Pipeline
# ══════════════════════════════════════════════════════════════════════════


class TestLiquidationAnalysis:
    @staticmethod
    def _make_sample_position(
        debt_usd: float = 50000.0,
        coll_usd: float = 100000.0,
        chain: str = "ethereum",
        protocol: str = "Aave V3",
    ) -> ProtocolPosition:
        coll = CollateralPosition(
            asset="WETH",
            asset_address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
            amount_usd=coll_usd,
            amount_token=coll_usd / 2800.0,
            ltv=0.76,
            liquidation_threshold=0.79,
            price_usd=2800.0,
        )
        debt = DebtPosition(
            asset="USDC",
            asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            amount_usd=debt_usd,
            amount_token=debt_usd,
            variable_rate=5.0,
        )
        pos = ProtocolPosition(
            protocol=protocol,
            chain=chain,
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            collateral=[coll],
            debt=[debt],
            total_collateral_usd=coll_usd,
            total_debt_usd=debt_usd,
        )
        pos.compute_health()
        return pos

    def test_empty_analysis(self):
        analysis = LiquidationAnalysis(
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            chains_analyzed=[],
        )
        analysis.analyze()
        assert analysis.total_collateral_usd == 0.0
        assert analysis.total_debt_usd == 0.0
        assert analysis.overall_health_factor is None
        assert len(analysis.cascade_scenarios) == 0
        assert len(analysis.liquidation_clusters) == 0

    def test_single_safe_position(self):
        pos = self._make_sample_position(debt_usd=10000.0, coll_usd=100000.0)
        analysis = LiquidationAnalysis(
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            chains_analyzed=["ethereum"],
            positions=[pos],
        )
        analysis.analyze()
        assert analysis.total_collateral_usd == 100000.0
        assert analysis.total_debt_usd == 10000.0
        assert analysis.overall_risk_tier == RiskTier.SAFE

    def test_multiple_chain_aggregation(self):
        pos1 = self._make_sample_position(debt_usd=80000.0, coll_usd=100000.0, chain="ethereum")
        pos2 = self._make_sample_position(debt_usd=5000.0, coll_usd=50000.0, chain="base")
        analysis = LiquidationAnalysis(
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            chains_analyzed=["ethereum", "base"],
            positions=[pos1, pos2],
        )
        analysis.analyze()
        assert analysis.total_collateral_usd == 150000.0
        assert analysis.total_debt_usd == 85000.0
        assert analysis.overall_risk_tier in (RiskTier.WATCH, RiskTier.DANGER)

    def test_cascade_scenarios_generated(self):
        # One critical position should generate cascade scenarios
        pos = self._make_sample_position(debt_usd=95000.0, coll_usd=100000.0)
        analysis = LiquidationAnalysis(
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            chains_analyzed=["ethereum"],
            positions=[pos],
        )
        analysis.analyze()
        assert len(analysis.cascade_scenarios) > 0

    def test_report_text_format(self):
        pos = self._make_sample_position(debt_usd=50000.0, coll_usd=100000.0)
        analysis = LiquidationAnalysis(
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            chains_analyzed=["ethereum"],
            positions=[pos],
        )
        analysis.analyze()
        report = analysis.report(format="text")
        assert "LIQUIDATION CASCADE RISK ANALYSIS" in report
        assert "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18" in report
        assert "POSITION BREAKDOWN" in report
        assert "OVERALL PORTFOLIO HEALTH" in report

    def test_report_json_format(self):
        pos = self._make_sample_position(debt_usd=50000.0, coll_usd=100000.0)
        analysis = LiquidationAnalysis(
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            chains_analyzed=["ethereum"],
            positions=[pos],
        )
        analysis.analyze()
        json_str = analysis.report(format="json")
        data = json.loads(json_str)
        assert data["wallet"] == "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18"
        assert "overall_risk_tier" in data
        assert "positions" in data
        assert len(data["positions"]) == 1

    def test_warnings_and_errors_in_report(self):
        analysis = LiquidationAnalysis(
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            chains_analyzed=["ethereum"],
            errors=["Failed to connect to RPC"],
            warnings=["Web3 unavailable"],
        )
        report = analysis.report()
        assert "Failed to connect to RPC" in report
        assert "Web3 unavailable" in report

    def test_invalid_address_analysis(self):
        """Verify the analyzer's _validate_address rejects bad addresses."""
        analyzer = LiquidationCascadeAnalyzer()
        assert not analyzer._validate_address("invalid-address")


# ══════════════════════════════════════════════════════════════════════════
# Helper Functions
# ══════════════════════════════════════════════════════════════════════════


class TestHelperFunctions:
    def test_estimate_asset_ltv_stablecoin(self):
        ltv, liq = _estimate_asset_ltv("USDC")
        assert ltv >= 0.78
        assert liq >= 0.83

    def test_estimate_asset_ltv_eth(self):
        ltv, liq = _estimate_asset_ltv("WETH")
        assert ltv == 0.80
        assert liq == 0.83

    def test_estimate_asset_ltv_unknown(self):
        ltv, liq = _estimate_asset_ltv("UNKNOWN_TOKEN")
        assert ltv == 0.50
        assert liq == 0.55

    def test_estimate_asset_price_known(self):
        assert _estimate_asset_price("ETH") == 2800.0
        assert _estimate_asset_price("USDC") == 1.0
        assert _estimate_asset_price("WBTC") == 68000.0

    def test_estimate_asset_price_unknown(self):
        assert _estimate_asset_price("UNKNOWN") == 1.0

    def test_resolve_asset_symbol_weth(self):
        addr = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
        assert _resolve_asset_symbol(addr, "ethereum") == "WETH"

    def test_resolve_asset_symbol_usdc_base(self):
        addr = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
        assert _resolve_asset_symbol(addr, "base") == "USDC"

    def test_resolve_asset_symbol_unknown(self):
        addr = "0xdead000000000000000000000000000000000000"
        sym = _resolve_asset_symbol(addr, "ethereum")
        assert "0xdead" in sym

    def test_validate_address_solana_variants(self):
        """Test various valid Solana address formats."""
        analyzer = LiquidationCascadeAnalyzer()
        valid_addresses = [
            "7EcDhSYGxXyscszYEp35KHN8vvw3svAuLKTzXwCFLrH",
            "DpRueBHHhrqMATHrYgvKQzFJFynfMFVPMgfzJgrXqKnQ",
            "So11111111111111111111111111111111111111112",
        ]
        for addr in valid_addresses:
            assert analyzer._validate_address(addr), f"Expected valid: {addr}"


# ══════════════════════════════════════════════════════════════════════════
# CascadeScenario Model
# ══════════════════════════════════════════════════════════════════════════


class TestCascadeScenario:
    def test_create_scenario(self):
        scenario = CascadeScenario(
            name="Test Crash",
            description="A test scenario",
            liquidated_positions=3,
            total_liquidated_value_usd=150000.0,
            secondary_affected_positions=5,
            total_secondary_value_usd=250000.0,
            market_impact_pct=0.15,
        )
        assert scenario.liquidated_positions == 3
        assert scenario.total_liquidated_value_usd == 150000.0
        assert scenario.market_impact_pct == 0.15

    def test_to_dict(self):
        scenario = CascadeScenario(
            name="10% Drop",
            description="Simulate 10% drop",
            liquidated_positions=2,
            total_liquidated_value_usd=50000.0,
        )
        d = scenario.to_dict()
        assert d["name"] == "10% Drop"
        assert d["liquidated_positions"] == 2


# ══════════════════════════════════════════════════════════════════════════
# LiquidationCluster Model
# ══════════════════════════════════════════════════════════════════════════


class TestLiquidationCluster:
    def test_create_cluster(self):
        cluster = LiquidationCluster(
            chain="ethereum",
            primary_collateral="WETH",
            price_range_low=1600.0,
            price_range_high=1800.0,
            wallet_count=5,
            total_debt_usd=500000.0,
            total_collateral_usd=1000000.0,
        )
        assert cluster.wallet_count == 5
        assert cluster.price_range_low == 1600.0

    def test_to_dict(self):
        cluster = LiquidationCluster(
            chain="base",
            primary_collateral="ETH",
            price_range_low=1500.0,
            price_range_high=1700.0,
            wallet_count=3,
            total_debt_usd=200000.0,
            total_collateral_usd=400000.0,
        )
        d = cluster.to_dict()
        assert d["chain"] == "base"
        assert d["wallet_count"] == 3


# ══════════════════════════════════════════════════════════════════════════
# RiskTier Enum
# ══════════════════════════════════════════════════════════════════════════


class TestRiskTier:
    def test_score_ordering(self):
        assert RiskTier.SAFE.score() == 0
        assert RiskTier.WATCH.score() == 1
        assert RiskTier.DANGER.score() == 2
        assert RiskTier.CRITICAL.score() == 3

    def test_string_values(self):
        assert RiskTier.SAFE.value == "SAFE"
        assert RiskTier.CRITICAL.value == "CRITICAL"

    def test_from_string(self):
        assert RiskTier("SAFE") == RiskTier.SAFE
        assert RiskTier("CRITICAL") == RiskTier.CRITICAL


# ══════════════════════════════════════════════════════════════════════════
# Edge Cases
# ══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_position_zero_collateral_no_health_factor(self):
        pos = ProtocolPosition(
            protocol="Aave V3",
            chain="ethereum",
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            collateral=[],
            debt=[],
            total_collateral_usd=0.0,
            total_debt_usd=0.0,
        )
        pos.compute_health()
        assert pos.health_factor == float("inf")
        assert pos.risk_tier == RiskTier.SAFE

    def test_position_with_debt_but_no_collateral(self):
        """Edge case: position with debt but zero collateral computed health."""
        debt = DebtPosition(
            asset="USDC",
            asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            amount_usd=5000.0,
            amount_token=5000.0,
            variable_rate=5.0,
        )
        pos = ProtocolPosition(
            protocol="Aave V3",
            chain="ethereum",
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            collateral=[],
            debt=[debt],
            total_collateral_usd=0.0,
            total_debt_usd=5000.0,
        )
        pos.compute_health()
        # With no collateral, health factor computation should handle gracefully
        assert pos.health_factor == float("inf")  # Division by zero avoided
        assert pos.risk_tier == RiskTier.SAFE

    def test_mixed_risk_positions_aggregation(self):
        """Multiple positions with different risk tiers."""
        safe = ProtocolPosition(
            protocol="Aave V3",
            chain="ethereum",
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            collateral=[
                CollateralPosition(
                    asset="WETH",
                    asset_address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
                    amount_usd=200000.0,
                    amount_token=71.43,
                    ltv=0.76,
                    liquidation_threshold=0.79,
                    price_usd=2800.0,
                )
            ],
            debt=[
                DebtPosition(
                    asset="USDC",
                    asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    amount_usd=10000.0,
                    amount_token=10000.0,
                    variable_rate=5.0,
                )
            ],
            total_collateral_usd=200000.0,
            total_debt_usd=10000.0,
        )
        safe.compute_health()

        critical = ProtocolPosition(
            protocol="Aave V3",
            chain="arbitrum",
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            collateral=[
                CollateralPosition(
                    asset="WETH",
                    asset_address="0x82af49447d8a07e3bd95bd0d56f35241523fbab1",
                    amount_usd=50000.0,
                    amount_token=17.86,
                    ltv=0.76,
                    liquidation_threshold=0.79,
                    price_usd=2800.0,
                )
            ],
            debt=[
                DebtPosition(
                    asset="USDC",
                    asset_address="0xaf88d065e77c8cc2239327c5edb3a432268e5831",
                    amount_usd=48000.0,
                    amount_token=48000.0,
                    variable_rate=6.0,
                )
            ],
            total_collateral_usd=50000.0,
            total_debt_usd=48000.0,
        )
        critical.compute_health()

        analysis = LiquidationAnalysis(
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            chains_analyzed=["ethereum", "arbitrum"],
            positions=[safe, critical],
        )
        analysis.analyze()

        assert safe.risk_tier == RiskTier.SAFE
        assert critical.risk_tier == RiskTier.CRITICAL
        # The large safe position ($200k coll, $10k debt) outweighs the
        # small critical position ($50k coll, $48k debt) in the weighted
        # average, so overall is SAFE - but cascade scenarios still show the risk
        assert analysis.overall_risk_tier == RiskTier.SAFE
        assert len(analysis.cascade_scenarios) > 0
        total_liquidated = sum(s.total_liquidated_value_usd for s in analysis.cascade_scenarios)
        assert total_liquidated > 0  # Cascade scenarios capture the critical position's risk

    def test_to_dict_serialization_full(self):
        """Ensure the full analysis serializes to dict without errors."""
        pos = self._make_sample_position(debt_usd=50000.0, coll_usd=100000.0)
        analysis = LiquidationAnalysis(
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            chains_analyzed=["ethereum"],
            positions=[pos],
            errors=["test error"],
            warnings=["test warning"],
        )
        analysis.analyze()
        d = analysis.to_dict()
        assert isinstance(d, dict)
        assert d["wallet"] == "0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18"
        assert len(d["positions"]) == 1
        assert "test error" in d["errors"]

    @staticmethod
    def _make_sample_position(debt_usd=50000.0, coll_usd=100000.0, chain="ethereum", protocol="Aave V3"):
        coll = CollateralPosition(
            asset="WETH",
            asset_address="0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
            amount_usd=coll_usd,
            amount_token=coll_usd / 2800.0,
            ltv=0.76,
            liquidation_threshold=0.79,
            price_usd=2800.0,
        )
        debt = DebtPosition(
            asset="USDC",
            asset_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            amount_usd=debt_usd,
            amount_token=debt_usd,
            variable_rate=5.0,
        )
        pos = ProtocolPosition(
            protocol=protocol,
            chain=chain,
            wallet="0x742d35Cc6634C0532925a3b844Bc9e7595f2bD18",
            collateral=[coll],
            debt=[debt],
            total_collateral_usd=coll_usd,
            total_debt_usd=debt_usd,
        )
        pos.compute_health()
        return pos
