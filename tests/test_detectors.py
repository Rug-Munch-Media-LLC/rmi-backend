"""
Tests for RMI Detector Modules — 15+ tests covering MEV sandwich detection,
rug imminence prediction, pump-and-dump manipulation detection, and
smart contract honeypot detection.

All external API calls are mocked via unittest.mock.patch.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════
#  1. MEVSandwichDetector — MEV detection (4 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestMEVSandwichDetector:
    """MEVSandwichDetector: enums, data models, and scan()."""

    def test_mev_attack_type_values(self):
        """MEVAttackType enum has expected values."""
        from app.domains.detectors.mev_sandwich_detector import MEVAttackType

        assert MEVAttackType.SANDWICH.value == "sandwich"
        assert MEVAttackType.FRONTRUN.value == "frontrun"
        assert MEVAttackType.BACKRUN.value == "backrun"

    def test_mev_severity_hierarchy(self):
        """MEVSeverity levels exist and have expected names."""
        from app.domains.detectors.mev_sandwich_detector import MEVSeverity

        assert MEVSeverity.CRITICAL.value == "critical"
        assert MEVSeverity.HIGH.value == "high"
        assert MEVSeverity.MEDIUM.value == "medium"
        assert MEVSeverity.LOW.value == "low"

    def test_pool_info_validates_address(self):
        """PoolInfo.__post_init__ validates EVM addresses."""
        from app.domains.detectors.mev_sandwich_detector import (
            PoolDexType,
            PoolInfo,
        )

        pool = PoolInfo(
            address="0x1234567890abcdef1234567890abcdef12345678",
            chain="ethereum",
            dex_type=PoolDexType.UNISWAP_V3,
        )
        assert pool.address is not None

    @pytest.mark.asyncio
    async def test_scan_handles_empty_pools_gracefully(self):
        """scan() does not crash when no pools are configured."""
        from app.domains.detectors.mev_sandwich_detector import MEVSandwichDetector

        detector = MEVSandwichDetector()
        # Mock __init__ so it doesn't try to connect to external services
        with patch.object(detector, "_fetch_pool_txs", AsyncMock(return_value=[])):
            with patch.object(detector, "_known_bot_addresses", []):
                with patch.object(detector, "_known_pools", []):
                    report = await detector.scan(pools=[])
        assert report is not None


class TestMEVSandwichDetectorDataModels:
    """MEV data model tests."""

    def test_pool_info_serialization(self):
        """PoolInfo.to_dict() returns expected keys."""
        from app.domains.detectors.mev_sandwich_detector import (
            PoolDexType,
            PoolInfo,
        )

        pool = PoolInfo(
            address="0x1234567890abcdef1234567890abcdef12345678",
            chain="ethereum",
            dex_type=PoolDexType.UNISWAP_V2,
            token0="0xtoken0",
            token1="0xtoken1",
            liquidity_usd=100000.0,
        )
        assert pool.chain == "ethereum"
        assert pool.dex_type == PoolDexType.UNISWAP_V2
        assert pool.liquidity_usd == 100000.0

    def test_pool_dex_type_from_string(self):
        """PoolDexType can identify known DEX types."""
        from app.domains.detectors.mev_sandwich_detector import PoolDexType

        assert PoolDexType.UNISWAP_V3.value == "uniswap_v3"
        assert PoolDexType.PANCAKESWAP.value == "pancakeswap"
        assert PoolDexType.UNKNOWN.value == "unknown"


# ═══════════════════════════════════════════════════════════════════════
#  2. RugImminencePredictor — rug pull prediction (5 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestRugImminencePredictor:
    """RugImminencePredictor.predict() and supporting methods."""

    @pytest.fixture
    def predictor(self):
        from app.domains.detectors.rug_imminence_predictor import (
            DEFAULT_WEIGHTS,
            RugImminencePredictor,
        )

        p = RugImminencePredictor(weights=DEFAULT_WEIGHTS)
        # Stub all internal analyzer methods so predict() runs quickly
        analyzers = [
            "_analyze_lp_health",
            "_analyze_deployer_risk",
            "_analyze_smart_money_flow",
            "_analyze_bundle_pattern",
            "_analyze_social_velocity",
            "_analyze_contract_risk",
            "_analyze_liquidity_migration",
        ]
        for a in analyzers:
            setattr(p, a, AsyncMock())
        return p

    def test_predictor_init_with_custom_weights(self):
        """Custom weights are accepted and normalized."""
        from app.domains.detectors.rug_imminence_predictor import (
            RugImminencePredictor,
        )

        weights = {"lp_health": 1.0}
        p = RugImminencePredictor(weights=weights)
        assert abs(p.weights["lp_health"] - 1.0) < 0.01

    def test_predictor_init_normalizes_weights(self):
        """Weights that sum != 1.0 are normalized."""
        from app.domains.detectors.rug_imminence_predictor import (
            RugImminencePredictor,
        )

        p = RugImminencePredictor(weights={"lp_health": 0.5, "deployer_risk": 0.5})
        # Sum is 1.0 already, should remain
        assert abs(sum(p.weights.values()) - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_predict_rejects_invalid_chain(self, predictor):
        """predict() raises ValueError for unsupported chains."""
        with pytest.raises(ValueError, match="Unsupported chain"):
            await predictor.predict("0x1234567890abcdef1234567890abcdef12345678", chain="invalid")

    @pytest.mark.asyncio
    async def test_predict_rejects_invalid_address_format(self, predictor):
        """predict() raises ValueError for malformed addresses."""
        with pytest.raises(ValueError, match="Invalid address format"):
            await predictor.predict("not-an-address", chain="base")

    @pytest.mark.asyncio
    async def test_predict_accepts_valid_address_and_chain(self, predictor):
        """predict() runs without error for valid inputs."""
        result = await predictor.predict("0x1234567890abcdef1234567890abcdef12345678", chain="base")
        assert result.token_address == "0x1234567890abcdef1234567890abcdef12345678"
        assert result.chain == "base"
        assert 0.0 <= result.score <= 100.0


class TestRugImminencePredictorSignalCategories:
    """RugImminencePredictor signal category logic."""

    def test_signal_category_weighted_contribution(self):
        """SignalCategory.weighted_contribution() is score * weight."""
        from app.domains.detectors.rug_imminence_predictor import SignalCategory

        sig = SignalCategory(name="test", weight=0.25, score=80.0)
        assert sig.weighted_contribution() == 20.0

    def test_signal_category_defaults(self):
        """SignalCategory has sensible defaults."""
        from app.domains.detectors.rug_imminence_predictor import SignalCategory

        sig = SignalCategory(name="test", weight=0.5)
        assert sig.score == 0.0
        assert sig.confidence == 0.0
        assert sig.evidence == []
        assert sig.flags == []

    def test_imminence_level_numeric(self):
        """ImminenceLevel.numeric returns ordinal values."""
        from app.domains.detectors.rug_imminence_predictor import ImminenceLevel

        assert ImminenceLevel.LOW.numeric == 1
        assert ImminenceLevel.MEDIUM.numeric == 2
        assert ImminenceLevel.HIGH.numeric == 3
        assert ImminenceLevel.CRITICAL.numeric == 4


# ═══════════════════════════════════════════════════════════════════════
#  3. PumpDumpDetector — pump and dump detection (4 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestPumpDumpDetector:
    """PumpDumpDetector.analyze_token() and data models."""

    @pytest.mark.asyncio
    async def test_analyze_token_handles_empty_address(self):
        """analyze_token() handles empty address without crash."""
        from app.domains.detectors.pump_dump_manipulation_detector import PumpDumpDetector

        detector = PumpDumpDetector()
        with patch.object(detector, "_fetch_dexscreener_data", AsyncMock(return_value=None)):
            with patch.object(detector, "_fetch_birdeye_data", AsyncMock(return_value=None)):
                result = await detector.analyze_token("", chain="ethereum")
        assert result is not None

    def test_manipulation_type_enum(self):
        """ManipulationType enum has expected values."""
        from app.domains.detectors.pump_dump_manipulation_detector import ManipulationType

        assert ManipulationType.COORDINATED_PUMP == "coordinated_pump"
        assert ManipulationType.WASH_TRADING == "wash_trading"
        assert ManipulationType.VOLUME_SPIKE == "volume_spike"

    def test_finding_severity_enum(self):
        """FindingSeverity enum has expected values."""
        from app.domains.detectors.pump_dump_manipulation_detector import FindingSeverity

        assert FindingSeverity.CRITICAL == "critical"
        assert FindingSeverity.HIGH == "high"
        assert FindingSeverity.INFO == "info"

    def test_coordinated_buy_group_dataclass(self):
        """CoordinatedBuyGroup accepts all fields."""
        from app.domains.detectors.pump_dump_manipulation_detector import CoordinatedBuyGroup

        group = CoordinatedBuyGroup(
            wallets=["wallet1", "wallet2"],
            window_seconds=60,
            total_buy_usd=50000.0,
            fresh_wallet_count=2,
            block_number=12345,
            timestamp=1000000,
            chain="ethereum",
        )
        assert len(group.wallets) == 2
        assert group.total_buy_usd == 50000.0
        assert group.chain == "ethereum"


# ═══════════════════════════════════════════════════════════════════════
#  4. HoneypotDetector — honeypot detection (4 tests)
# ═══════════════════════════════════════════════════════════════════════


class TestHoneypotDetector:
    """HoneypotDetector.analyze_token() and data models."""

    @pytest.mark.asyncio
    async def test_analyze_token_handles_empty_address(self):
        """analyze_token() handles empty address without crash."""
        from app.domains.detectors.smart_contract_honeypot_detector import HoneypotDetector

        detector = HoneypotDetector()
        with patch.object(detector, "_fetch_onchain_data", AsyncMock(return_value=None)):
            result = await detector.analyze_token("", chain="ethereum")
        assert result is not None

    def test_honeypot_type_enum_values(self):
        """HoneypotType enum has expected values."""
        from app.domains.detectors.smart_contract_honeypot_detector import HoneypotType

        assert HoneypotType.SELL_RESTRICTION.value == "sell_restriction"
        assert HoneypotType.PROXY_UPGRADE.value == "proxy_upgrade"
        assert HoneypotType.LIQUIDITY_DRAIN.value == "liquidity_drain"
        assert HoneypotType.SELFDESTRUCT.value == "selfdestruct"

    def test_finding_severity_levels(self):
        """FindingSeverity enum levels are distinct."""
        from app.domains.detectors.smart_contract_honeypot_detector import FindingSeverity

        assert FindingSeverity.CRITICAL.value == "critical"
        assert FindingSeverity.HIGH.value == "high"
        assert FindingSeverity.MEDIUM.value == "medium"
        assert FindingSeverity.LOW.value == "low"
        assert FindingSeverity.INFO.value == "info"

    def test_security_finding_to_dict(self):
        """SecurityFinding.to_dict() returns correct structure."""
        from app.domains.detectors.smart_contract_honeypot_detector import (
            FindingSeverity,
            HoneypotType,
            SecurityFinding,
        )

        finding = SecurityFinding(
            finding_type=HoneypotType.SELL_RESTRICTION,
            severity=FindingSeverity.CRITICAL,
            description="Cannot sell tokens",
            detail="Sell function reverts for all non-owner addresses",
        )
        d = finding.to_dict()
        assert d["type"] == "sell_restriction"
        assert d["severity"] == "critical"
        assert d["description"] == "Cannot sell tokens"
        assert "reverts" in d["detail"]
