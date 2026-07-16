"""
Tests for Flash Loan Attack Detector
=====================================
Covers core detection heuristics: flash loan identification, attack type
classification, severity scoring, sophistication analysis, and trace
parsing - all without requiring network calls.
"""

import unittest
from datetime import UTC, datetime

import pytest

from app.flash_loan_attack_detector import (
    AAVE_V2_FLASHLOAN_SIG,
    BALANCER_FLASHLOAN_SIG,
    FLASHLOAN_PROVIDERS,
    FLASHLOAN_SIGNATURES,
    AttackSeverity,
    AttackType,
    DetectionMethod,
    FlashLoanAttack,
    FlashLoanAttackDetector,
    FlashLoanCall,
    FlashLoanProtocol,
    TransactionTrace,
    _calculate_severity,
    _detect_attack_type,
    _is_known_flashloan_provider,
    _resolve_shared_aave_sig,
    _score_sophistication,
)


class TestEnumsAndConstants(unittest.TestCase):
    """Enum and type classification."""

    def test_severity_ordering(self):
        """Severity levels have correct values."""
        assert AttackSeverity.CRITICAL.value == "critical"
        assert AttackSeverity.HIGH.value == "high"
        assert AttackSeverity.MEDIUM.value == "medium"
        assert AttackSeverity.LOW.value == "low"
        assert AttackSeverity.INFO.value == "info"

    def test_attack_types(self):
        """All expected attack types are present."""
        types = {t.value for t in AttackType}
        expected = {
            "price_oracle_manipulation",
            "lp_drain",
            "arbitrage",
            "governance_attack",
            "liquidation_manipulation",
            "cross_protocol_chain",
            "self_liquidation",
            "reentrancy_exploit",
            "borrow_manipulation",
            "synthetic_position",
            "unknown",
        }
        for e in expected:
            assert e in types, f"Missing attack type: {e}"

    def test_flashloan_protocols(self):
        """All expected flash loan protocols are present."""
        protocols = {p.value for p in FlashLoanProtocol}
        expected = {
            "aave_v2",
            "aave_v3",
            "dydx",
            "uniswap_v3",
            "balancer",
            "euler",
            "radiant",
            "spark",
            "maker",
            "morpho",
            "silo",
            "unknown",
        }
        for e in expected:
            assert e in protocols, f"Missing protocol: {e}"

    def test_flashloan_signatures(self):
        """All major flash loan signatures are defined."""
        assert "0xab9c4b5d" in FLASHLOAN_SIGNATURES
        assert "0x42b0b77c" in FLASHLOAN_SIGNATURES
        assert "0x490e6c32" in FLASHLOAN_SIGNATURES
        assert "0x52b0f4c1" in FLASHLOAN_SIGNATURES

    def test_ethereum_providers_defined(self):
        """Ethereum has flash loan providers configured."""
        assert "ethereum" in FLASHLOAN_PROVIDERS
        assert len(FLASHLOAN_PROVIDERS["ethereum"]) > 5

    def test_detection_methods(self):
        """All detection methods have correct values."""
        assert DetectionMethod.DIRECT_CALL.value == "direct_call"
        assert DetectionMethod.LIFECYCLE_PATTERN.value == "lifecycle_pattern"


class TestFlashLoanCall(unittest.TestCase):
    """FlashLoanCall data model."""

    def test_valid_call(self):
        """Creating a valid flash loan call."""
        call = FlashLoanCall(
            protocol=FlashLoanProtocol.AAVE_V3,
            provider_address="0x87870Bca3F3fD6335C3F4cE8392D69350B4fA4E2",
            chain="ethereum",
            block_number=20000000,
            tx_index=5,
            amount_borrowed_usd=1_000_000.0,
            amount_repaid_usd=1_001_000.0,
        )
        assert call.protocol == FlashLoanProtocol.AAVE_V3
        assert call.amount_borrowed_usd == 1000000.0
        self.assertAlmostEqual(call.profit_usd(), -1000.0)  # repaid more than borrowed

    def test_negative_amount_raises(self):
        """Negative borrow amount should raise ValueError."""
        with pytest.raises(ValueError):
            FlashLoanCall(
                protocol=FlashLoanProtocol.UNISWAP_V3,
                provider_address="0xdead",
                chain="ethereum",
                block_number=1,
                tx_index=0,
                amount_borrowed_usd=-100.0,
            )

    def test_summary_format(self):
        """Summary produces expected format."""
        call = FlashLoanCall(
            protocol=FlashLoanProtocol.AAVE_V3,
            provider_address="0x87870Bca3F3fD6335C3F4cE8392D69350B4fA4E2",
            chain="ethereum",
            block_number=20000000,
            tx_index=5,
            amount_borrowed_usd=500_000.0,
            token_symbol="USDC",
        )
        summary = call.summary()
        assert "AAVE_V3" in summary
        assert "500,000" in summary
        assert "USDC" in summary


class TestTransactionTrace(unittest.TestCase):
    """TransactionTrace parsing and detection."""

    def setUp(self):
        self.valid_trace = TransactionTrace(
            tx_hash="0x" + "ab" * 32,
            chain="ethereum",
            block_number=20000000,
            tx_index=1,
            from_address="0xattacker00000000000000000000000000000001",
            to_address="0x7d2768De32b0b80b7a3454c06BdAc94A69DDc7A9",
            value_eth=0.0,
            gas_price_gwei=50.0,
            gas_used=250000,
            input_data=AAVE_V2_FLASHLOAN_SIG + "0" * 200,
        )

    def test_flashloan_sig_detection(self):
        """Trace with flash loan signature detected correctly."""
        assert self.valid_trace.has_flashloan_sig()

    def test_no_flashloan_sig(self):
        """Trace without flash loan signature."""
        trace = TransactionTrace(
            tx_hash="0x" + "cd" * 32,
            chain="ethereum",
            block_number=20000000,
            tx_index=2,
            from_address="0xnormal0000000000000000000000000000000001",
            to_address="0xnormalcontract0000000000000000000000001",
            input_data="0xa9059cbb" + "0" * 100,  # ERC20 transfer
        )
        assert not trace.has_flashloan_sig()

    def test_flashloan_protocol_identification(self):
        """Correct protocol identified from signature."""
        # Use a unique signature (BALANCER_V2) to avoid signature conflicts
        trace = TransactionTrace(
            tx_hash="0x" + "ab" * 32,
            chain="ethereum",
            block_number=20000000,
            tx_index=1,
            from_address="0xattacker00000000000000000000000000000001",
            to_address="0x7d2768De32b0b80b7a3454c06BdAc94A69DDc7A9",
            value_eth=0.0,
            gas_price_gwei=50.0,
            gas_used=250000,
            input_data=BALANCER_FLASHLOAN_SIG + "0" * 200,
        )
        protocol = trace.flashloan_protocol()
        assert protocol == FlashLoanProtocol.BALANCER

    def test_unknown_sig_returns_none(self):
        """Unknown signature returns None."""
        trace = TransactionTrace(
            tx_hash="0x" + "ef" * 32,
            chain="ethereum",
            block_number=1,
            tx_index=0,
            from_address="0x00",
            to_address="0x01",
            input_data="0xdeadbeef" + "0" * 100,
        )
        assert trace.flashloan_protocol() is None

    def test_short_input_data(self):
        """Short input data doesn't crash."""
        trace = TransactionTrace(
            tx_hash="0x" + "01" * 32,
            chain="ethereum",
            block_number=1,
            tx_index=0,
            from_address="0x00",
            to_address="0x01",
            input_data="0x1234",
        )
        assert not trace.has_flashloan_sig()
        assert trace.flashloan_protocol() is None


class TestDetectionHeuristics(unittest.TestCase):
    """Core detection logic."""

    def test_oracle_manipulation_detection(self):
        """Oracle manipulation is detected when oracle patterns present."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.AAVE_V3,
                provider_address="0xaave",
                chain="ethereum",
                block_number=1,
                tx_index=0,
                amount_borrowed_usd=1_000_000.0,
            )
        ]
        trace = TransactionTrace(
            tx_hash="0x" + "ab" * 32,
            chain="ethereum",
            block_number=1,
            tx_index=0,
            from_address="0xattacker",
            to_address="0xpool",
            input_data="0x1234" + "oracle" * 10 + "price" * 10 + "twap" * 5,
        )
        attack_type, confidence = _detect_attack_type(flash_loans, trace)
        assert attack_type == AttackType.PRICE_ORACLE_MANIPULATION
        assert confidence > 0.5

    def test_arbitrage_is_default(self):
        """Normal flash loan without suspicious patterns defaults to arbitrage."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.UNISWAP_V3,
                provider_address="0xuni",
                chain="ethereum",
                block_number=1,
                tx_index=0,
                amount_borrowed_usd=100_000.0,
            )
        ]
        trace = TransactionTrace(
            tx_hash="0x" + "cd" * 32,
            chain="ethereum",
            block_number=1,
            tx_index=0,
            from_address="0xtrader",
            to_address="0xpool",
            input_data="0x1234" + "normal" * 5,
        )
        attack_type, confidence = _detect_attack_type(flash_loans, trace)
        assert attack_type == AttackType.ARBITRAGE
        self.assertAlmostEqual(confidence, 0.5)

    def test_governance_attack_detection(self):
        """Governance-related contracts trigger governance attack classification."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.AAVE_V2,
                provider_address="0xaave",
                chain="ethereum",
                block_number=1,
                tx_index=0,
                amount_borrowed_usd=10_000_000.0,
            )
        ]
        trace = TransactionTrace(
            tx_hash="0x" + "ef" * 32,
            chain="ethereum",
            block_number=1,
            tx_index=0,
            from_address="0xgovattacker",
            to_address="0xgov",
            input_data="0x1234" + "propose" * 5 + "vote" * 3 + "execute" * 2,
        )
        attack_type, confidence = _detect_attack_type(flash_loans, trace)
        assert attack_type == AttackType.GOVERNANCE_ATTACK
        assert confidence > 0.5

    def test_lp_drain_detection(self):
        """Multiple flash loans with LP interaction triggers LP drain."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.BALANCER,
                provider_address="0xbal",
                chain="ethereum",
                block_number=1,
                tx_index=0,
                amount_borrowed_usd=5_000_000.0,
            ),
            FlashLoanCall(
                protocol=FlashLoanProtocol.AAVE_V3,
                provider_address="0xaave",
                chain="ethereum",
                block_number=1,
                tx_index=0,
                amount_borrowed_usd=3_000_000.0,
            ),
        ]
        trace = TransactionTrace(
            tx_hash="0x" + "01" * 32,
            chain="ethereum",
            block_number=1,
            tx_index=0,
            from_address="0xattacker",
            to_address="0xpool",
            input_data="0x1234" + "removeLiquidity" * 3 + "withdraw" * 2 + "burn" * 2,
        )
        attack_type, confidence = _detect_attack_type(flash_loans, trace)
        assert attack_type == AttackType.LP_DRAIN
        assert confidence > 0.5

    def test_cross_protocol_chain_detection(self):
        """Three or more flash loans trigger cross-protocol classification."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.AAVE_V3,
                provider_address="0x1",
                chain="ethereum",
                block_number=1,
                tx_index=0,
            ),
            FlashLoanCall(
                protocol=FlashLoanProtocol.UNISWAP_V3,
                provider_address="0x2",
                chain="ethereum",
                block_number=1,
                tx_index=0,
            ),
            FlashLoanCall(
                protocol=FlashLoanProtocol.BALANCER,
                provider_address="0x3",
                chain="ethereum",
                block_number=1,
                tx_index=0,
            ),
            FlashLoanCall(
                protocol=FlashLoanProtocol.DYDX,
                provider_address="0x4",
                chain="ethereum",
                block_number=1,
                tx_index=0,
            ),
        ]
        trace = TransactionTrace(
            tx_hash="0x" + "02" * 32,
            chain="ethereum",
            block_number=1,
            tx_index=0,
            from_address="0xattacker",
            to_address="0xpool",
            input_data="0x1234",
        )
        attack_type, confidence = _detect_attack_type(flash_loans, trace)
        assert attack_type == AttackType.CROSS_PROTOCOL_CHAIN
        assert confidence > 0.5


class TestSeverityScoring(unittest.TestCase):
    """Attack severity calculation."""

    def test_critical_over_1m(self):
        """Losses over $1M are critical."""
        severity = _calculate_severity(
            attacker_profit=1_500_000,
            victim_loss=500_000,
            sophistication=5.0,
        )
        assert severity == AttackSeverity.CRITICAL

    def test_critical_high_sophistication(self):
        """High sophistication alone can trigger critical."""
        severity = _calculate_severity(
            attacker_profit=100_000,
            victim_loss=50_000,
            sophistication=9.0,
        )
        assert severity == AttackSeverity.CRITICAL

    def test_high_between_100k_and_1m(self):
        """Losses between $100K and $1M are high."""
        severity = _calculate_severity(
            attacker_profit=500_000,
            victim_loss=0,
            sophistication=4.0,
        )
        assert severity == AttackSeverity.HIGH

    def test_medium_between_10k_and_100k(self):
        """Losses between $10K and $100K are medium."""
        severity = _calculate_severity(
            attacker_profit=50_000,
            victim_loss=0,
            sophistication=3.0,
        )
        assert severity == AttackSeverity.MEDIUM

    def test_low_under_10k(self):
        """Losses under $10K are low."""
        severity = _calculate_severity(
            attacker_profit=5_000,
            victim_loss=0,
            sophistication=2.0,
        )
        assert severity == AttackSeverity.LOW

    def test_info_no_loss(self):
        """No profit/loss gives info severity."""
        severity = _calculate_severity(
            attacker_profit=0,
            victim_loss=0,
            sophistication=0.0,
        )
        assert severity == AttackSeverity.INFO


class TestSophisticationScoring(unittest.TestCase):
    """Attack sophistication scoring."""

    def test_single_flashloan_low_sophistication(self):
        """Single flash loan has low sophistication."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.UNISWAP_V3,
                provider_address="0x1",
                chain="ethereum",
                block_number=1,
                tx_index=0,
            )
        ]
        score = _score_sophistication(
            flash_loans,
            [],
            AttackType.ARBITRAGE,
        )
        assert score <= 3.0

    def test_multiple_flashloans_higher_sophistication(self):
        """Multiple flash loans increase sophistication."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.AAVE_V3,
                provider_address="0x1",
                chain="ethereum",
                block_number=1,
                tx_index=0,
            ),
            FlashLoanCall(
                protocol=FlashLoanProtocol.UNISWAP_V3,
                provider_address="0x2",
                chain="ethereum",
                block_number=1,
                tx_index=0,
            ),
            FlashLoanCall(
                protocol=FlashLoanProtocol.BALANCER,
                provider_address="0x3",
                chain="ethereum",
                block_number=1,
                tx_index=0,
            ),
        ]
        score = _score_sophistication(
            flash_loans,
            [DetectionMethod.LIFECYCLE_PATTERN, DetectionMethod.ORACLE_DEVIATION],
            AttackType.CROSS_PROTOCOL_CHAIN,
        )
        assert score > 4.0

    def test_max_sophistication_capped(self):
        """Sophistication score is capped at 10.0."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.AAVE_V3,
                provider_address="0x1",
                chain="ethereum",
                block_number=1,
                tx_index=0,
            )
            for _ in range(10)
        ]
        score = _score_sophistication(
            flash_loans,
            [
                DetectionMethod.MULTIPLE_CALLS,
                DetectionMethod.ORACLE_DEVIATION,
                DetectionMethod.LIFECYCLE_PATTERN,
            ],
            AttackType.GOVERNANCE_ATTACK,
        )
        assert score <= 10.0


class TestFlashLoanAttackModel(unittest.TestCase):
    """FlashLoanAttack data model."""

    def test_valid_attack(self):
        """Creating a valid attack with all fields."""
        now = datetime.now(UTC).isoformat()
        attack = FlashLoanAttack(
            chain="ethereum",
            block_number=20000000,
            tx_hash="0x" + "ab" * 32,
            attacker_address="0xattacker00000000000000000000000000000001",
            attack_type=AttackType.PRICE_ORACLE_MANIPULATION,
            protocol_used=FlashLoanProtocol.AAVE_V3,
            total_borrowed_usd=5_000_000.0,
            attacker_profit_usd=1_200_000.0,
            victim_loss_usd=4_000_000.0,
            severity=AttackSeverity.CRITICAL,
            confidence=0.85,
            tags=["price_oracle_manipulation", "flash_loan_used"],
            detected_at=now,
        )
        assert attack.chain == "ethereum"
        assert attack.severity == AttackSeverity.CRITICAL
        self.assertAlmostEqual(attack.total_borrowed_usd, 5_000_000.0)

    def test_negative_profit_raises(self):
        """Negative profit should raise ValueError."""
        with pytest.raises(ValueError):
            FlashLoanAttack(
                chain="ethereum",
                block_number=1,
                tx_hash="0x" + "01" * 32,
                attacker_address="0x00",
                attacker_profit_usd=-100.0,
            )

    def test_summary_format(self):
        """Attack summary produces expected format."""
        attack = FlashLoanAttack(
            chain="ethereum",
            block_number=20000000,
            tx_hash="0x" + "ab" * 32,
            attacker_address="0xattacker00000000000000000000000000000001",
            attack_type=AttackType.PRICE_ORACLE_MANIPULATION,
            protocol_used=FlashLoanProtocol.AAVE_V3,
            attacker_profit_usd=1_200_000.0,
            victim_loss_usd=4_000_000.0,
            severity=AttackSeverity.CRITICAL,
        )
        summary = attack.summary()
        assert "CRITICAL" in summary
        assert "FLASH LOAN ATTACK" in summary
        assert "1,200,000" in summary
        assert "4,000,000" in summary

    def test_to_dict_serialization(self):
        """to_dict produces JSON-serializable output."""
        attack = FlashLoanAttack(
            chain="ethereum",
            block_number=20000000,
            tx_hash="0x" + "ab" * 32,
            attacker_address="0xattacker00000000000000000000000000000001",
            attack_type=AttackType.LP_DRAIN,
            protocol_used=FlashLoanProtocol.BALANCER,
            attacker_profit_usd=500_000.0,
            victim_loss_usd=2_000_000.0,
            severity=AttackSeverity.HIGH,
        )
        d = attack.to_dict()
        assert d["type"] == "flash_loan_attack"
        assert d["chain"] == "ethereum"
        assert d["attack_type"] == "lp_drain"
        assert d["protocol"] == "balancer"
        assert d["severity"] == "high"
        self.assertAlmostEqual(d["attacker_profit_usd"], 500_000.0)


class TestProviderIdentification(unittest.TestCase):
    """Known flash loan provider identification."""

    def test_known_provider_matches(self):
        """Known Aave V2 address is identified on Ethereum."""
        result = _is_known_flashloan_provider(
            "0x7d2768De32b0b80b7a3454c06BdAc94A69DDc7A9",
            "ethereum",
        )
        assert result

    def test_unknown_provider_returns_false(self):
        """Unknown address returns False."""
        result = _is_known_flashloan_provider(
            "0x0000000000000000000000000000000000000001",
            "ethereum",
        )
        assert not result

    def test_case_insensitive(self):
        """Address matching is case-insensitive."""
        result = _is_known_flashloan_provider(
            "0x7D2768DE32B0B80B7A3454C06BDAC94A69DDC7A9",  # uppercase
            "ethereum",
        )
        assert result

    def test_chain_specific_providers(self):
        """Chain specific providers (e.g., Base) are resolved correctly."""
        assert "base" in FLASHLOAN_PROVIDERS
        assert len(FLASHLOAN_PROVIDERS["base"]) > 0


class TestSharedSigResolution(unittest.TestCase):
    """Shared AAVE signature disambiguation."""

    def test_aave_v2_by_address(self):
        """Known Aave V2 address resolves to AAVE_V2."""
        result = _resolve_shared_aave_sig(
            "0x7d2768De32b0b80b7a3454c06BdAc94A69DDc7A9",
            "ethereum",
        )
        assert result == FlashLoanProtocol.AAVE_V2

    def test_aave_v3_by_address(self):
        """Known Aave V3 address resolves to AAVE_V3."""
        result = _resolve_shared_aave_sig(
            "0x87870Bca3F3fD6335C3F4cE8392D69350B4fA4E2",
            "ethereum",
        )
        assert result == FlashLoanProtocol.AAVE_V3

    def test_unknown_address_returns_unknown(self):
        """Unrecognized provider address returns UNKNOWN."""
        result = _resolve_shared_aave_sig(
            "0x0000000000000000000000000000000000000000",
            "ethereum",
        )
        assert result == FlashLoanProtocol.UNKNOWN

    def test_case_insensitive_resolution(self):
        """Address matching is case-insensitive."""
        result = _resolve_shared_aave_sig(
            "0x7D2768DE32B0B80B7A3454C06BDAC94A69DDC7A9",
            "ethereum",
        )
        assert result == FlashLoanProtocol.AAVE_V2


class TestDetectorInitialization(unittest.TestCase):
    """FlashLoanAttackDetector initialization."""

    def test_default_chains(self):
        """Default initialization covers all supported chains."""
        detector = FlashLoanAttackDetector()
        assert len(detector.chains) > 5
        assert "ethereum" in detector.chains
        assert "bsc" in detector.chains

    def test_custom_chains(self):
        """Custom chain list is used."""
        detector = FlashLoanAttackDetector(chains=["ethereum", "base"])
        assert detector.chains == ["ethereum", "base"]

    def test_cache_starts_empty(self):
        """Cache is empty on initialization."""
        detector = FlashLoanAttackDetector()
        assert len(detector._cache) == 0

    def test_clear_cache(self):
        """Clearing cache works correctly."""
        detector = FlashLoanAttackDetector()
        detector._cache["test"] = []
        count = detector.clear_cache()
        assert count == 1
        assert len(detector._cache) == 0


class TestInternalMethods(unittest.TestCase):
    """Internal helper methods."""

    def setUp(self):
        self.detector = FlashLoanAttackDetector()

    def test_extract_amount_from_input(self):
        """Amount extraction from flash loan calldata."""
        # Aave flashLoan sig + 32 bytes amount
        input_data = AAVE_V2_FLASHLOAN_SIG + "00" * 32 + "ff" * 32
        amount = self.detector._extract_amount_from_input(input_data)
        assert amount == "0x" + "00" * 32

    def test_extract_amount_short_input(self):
        """Short input returns '0'."""
        amount = self.detector._extract_amount_from_input("0x1234")
        assert amount == "0"

    def test_identify_known_provider(self):
        """Known provider address is identified."""
        protocol = self.detector._identify_provider(
            "0x7d2768De32b0b80b7a3454c06BdAc94A69DDc7A9",
            "ethereum",
        )
        assert protocol == FlashLoanProtocol.AAVE_V2

    def test_identify_unknown_provider(self):
        """Unknown address returns None."""
        protocol = self.detector._identify_provider(
            "0x0000000000000000000000000000000000000001",
            "ethereum",
        )
        assert protocol is None

    def test_generate_tags(self):
        """Tags generation includes all expected tags."""
        tags = self.detector._generate_tags(
            AttackType.PRICE_ORACLE_MANIPULATION,
            [
                FlashLoanCall(
                    protocol=FlashLoanProtocol.AAVE_V3,
                    provider_address="0x1",
                    chain="ethereum",
                    block_number=1,
                    tx_index=0,
                ),
                FlashLoanCall(
                    protocol=FlashLoanProtocol.UNISWAP_V3,
                    provider_address="0x2",
                    chain="ethereum",
                    block_number=1,
                    tx_index=0,
                ),
            ],
            [DetectionMethod.DIRECT_CALL, DetectionMethod.ORACLE_DEVIATION],
        )
        assert "price_oracle_manipulation" in tags
        assert "flash_loan_used" in tags
        assert "cross_protocol" in tags
        assert "oracle_attack" in tags

    def test_generate_timeline(self):
        """Timeline generation includes relevant events."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.AAVE_V3,
                provider_address="0x1",
                chain="ethereum",
                block_number=100,
                tx_index=0,
                amount_borrowed_usd=500_000.0,
            ),
        ]
        trace = TransactionTrace(
            tx_hash="0x" + "01" * 32,
            chain="ethereum",
            block_number=100,
            tx_index=5,
            from_address="0xattacker",
            to_address="0xpool",
            internal_calls=[{"to": "0xswap"}],
        )
        timeline = self.detector._generate_timeline(flash_loans, trace)
        assert len(timeline) > 2
        assert any("Step 1" in t for t in timeline)
        assert any("Internal calls" in t for t in timeline)


class TestHighValueAttackScenarios(unittest.TestCase):
    """Real-world attack scenario simulations."""

    def test_typical_flash_loan_arbitrage(self):
        """Normal arbitrage should NOT be flagged as an attack."""
        detector = FlashLoanAttackDetector()
        trace = TransactionTrace(
            tx_hash="0x" + "aa" * 32,
            chain="ethereum",
            block_number=20000000,
            tx_index=3,
            from_address="0xmevbot0000000000000000000000000000000001",
            to_address="0x7d2768De32b0b80b7a3454c06BdAc94A69DDc7A9",
            input_data=AAVE_V2_FLASHLOAN_SIG + "00" * 100,
            internal_calls=[
                {"to": "0xuniswap", "input": "0x"},
                {"to": "0xcurve", "input": "0x"},
            ],
        )

        # Simulate analysis
        flash_loans = detector._find_flash_loans(trace)
        attack_type, _confidence = _detect_attack_type(flash_loans, trace)

        # Without oracle/LP patterns, should default to arbitrage
        assert attack_type == AttackType.ARBITRAGE

    def test_sophisticated_oracle_attack_scenario(self):
        """Sophisticated multi-step oracle manipulation should score high."""
        flash_loans = [
            FlashLoanCall(
                protocol=FlashLoanProtocol.AAVE_V3,
                provider_address="0x1",
                chain="ethereum",
                block_number=1,
                tx_index=0,
                amount_borrowed_usd=10_000_000.0,
            ),
            FlashLoanCall(
                protocol=FlashLoanProtocol.UNISWAP_V3,
                provider_address="0x2",
                chain="ethereum",
                block_number=1,
                tx_index=0,
                amount_borrowed_usd=5_000_000.0,
            ),
        ]
        trace = TransactionTrace(
            tx_hash="0x" + "bb" * 32,
            chain="ethereum",
            block_number=1,
            tx_index=0,
            from_address="0xattacker",
            to_address="0xmanipulator",
            input_data="0x" + "oracle" * 20 + "price" * 20 + "twap" * 10 + "getRoundData" * 10,
            internal_calls=[{"to": "0xpricefeed"}, {"to": "0xlp"}, {"to": "0xdex"}] * 5,
        )

        attack_type, confidence = _detect_attack_type(flash_loans, trace)
        assert attack_type == AttackType.PRICE_ORACLE_MANIPULATION
        assert confidence > 0.7

        # Profit > $1M should be critical
        severity = _calculate_severity(
            attacker_profit=2_000_000,
            victim_loss=8_000_000,
            sophistication=8.5,
        )
        assert severity == AttackSeverity.CRITICAL


if __name__ == "__main__":
    unittest.main()
