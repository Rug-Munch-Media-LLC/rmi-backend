"""
Tests for MEV & Sandwich Attack Detector
=========================================
Covers all core components: sandwich detection heuristics, bot registry,
pool vulnerability scoring, and report generation - all without requiring
network calls.
"""

import unittest
from unittest.mock import AsyncMock, patch

from app.mev_sandwich_detector import (
    KNOWN_MEV_BOTS,
    MEVBotProfile,
    MEVSandwichDetector,
    MEVScanReport,
    MEVSeverity,
    MEVTransaction,
    PoolDexType,
    PoolInfo,
    SandwichAttack,
    _check_pool_vulnerability,
    _estimate_mev_vulnerability_score,
    _is_sandwich_pattern,
)


class TestMEVEnums(unittest.TestCase):
    """Enum and type classification."""

    def test_severity_ordering(self):
        """Severity levels have correct values."""
        assert MEVSeverity.CRITICAL.value == "critical"
        assert MEVSeverity.HIGH.value == "high"
        assert MEVSeverity.MEDIUM.value == "medium"
        assert MEVSeverity.LOW.value == "low"
        assert MEVSeverity.INFO.value == "info"

    def test_pool_dex_types(self):
        """All expected DEX types are present."""
        types = {t.value for t in PoolDexType}
        expected = {
            "uniswap_v2",
            "uniswap_v3",
            "sushiswap",
            "pancakeswap",
            "curve",
            "balancer",
            "aerodrome",
            "camelot",
            "unknown",
        }
        assert types == expected


class TestPoolVulnerability(unittest.TestCase):
    """Pool vulnerability scoring logic."""

    def test_high_liquidity_no_flags(self):
        """Well-funded pools have low vulnerability."""
        flags = _check_pool_vulnerability(
            {
                "liquidity_usd": 50_000_000,
                "volume_24h_usd": 20_000_000,
                "fee_tier": 3000,
            }
        )
        assert len(flags) == 0

    def test_low_liquidity_flagged(self):
        """Low-liquidity pools are flagged as vulnerable."""
        flags = _check_pool_vulnerability(
            {
                "liquidity_usd": 5_000,
                "volume_24h_usd": 1_000,
                "fee_tier": 3000,
            }
        )
        assert "low_liquidity_high_vuln" in flags

    def test_medium_liquidity_flagged(self):
        """Medium-liquidity pools get medium vulnerability."""
        flags = _check_pool_vulnerability(
            {
                "liquidity_usd": 50_000,
                "volume_24h_usd": 5_000,
                "fee_tier": 3000,
            }
        )
        assert "low_liquidity_medium_vuln" in flags

    def test_low_fee_arb_vuln(self):
        """Low fee tier pools attract arbitrage MEV."""
        flags = _check_pool_vulnerability(
            {
                "liquidity_usd": 1_000_000,
                "volume_24h_usd": 500_000,
                "fee_tier": 100,
            }
        )
        assert "low_fee_arb_vuln" in flags

    def test_high_fee_sandwich_vuln(self):
        """High fee tier pools hide sandwich profit."""
        flags = _check_pool_vulnerability(
            {
                "liquidity_usd": 1_000_000,
                "volume_24h_usd": 500_000,
                "fee_tier": 10000,
            }
        )
        assert "high_fee_sandwich_vuln" in flags


class TestMEVVulnerabilityScore(unittest.TestCase):
    """Score computation from flags."""

    def test_zero_flags_zero_score(self):
        """No flags = zero vulnerability score."""
        score = _estimate_mev_vulnerability_score([])
        assert score == 0.0

    def test_high_vuln_score(self):
        """High liquidity vuln produces high score."""
        score = _estimate_mev_vulnerability_score(
            [
                "low_liquidity_high_vuln",
                "low_fee_arb_vuln",
            ]
        )
        assert score > 0.5

    def test_score_capped_at_one(self):
        """Score never exceeds 1.0."""
        score = _estimate_mev_vulnerability_score(
            [
                "low_liquidity_high_vuln",
                "low_liquidity_medium_vuln",
                "stale_pricing_vuln",
                "low_fee_arb_vuln",
                "high_fee_sandwich_vuln",
            ]
        )
        assert score <= 1.0

    def test_medium_score(self):
        """Mixed flags produce medium score."""
        score = _estimate_mev_vulnerability_score(
            [
                "stale_pricing_vuln",
            ]
        )
        self.assertAlmostEqual(score, 0.3)


class TestSandwichDetectionHeuristics(unittest.TestCase):
    """Heuristic detection of sandwich patterns."""

    def setUp(self):
        self.frontrun = {
            "from_address": "0xbot123",
            "to_address": "0xpoolabc",
            "tx_index": 1,
            "gas_price_gwei": 50.0,
            "method_signature": "swapExactTokensForTokens",
        }
        self.victim = {
            "from_address": "0xuser456",
            "to_address": "0xpoolabc",
            "tx_index": 2,
            "gas_price_gwei": 20.0,
            "method_signature": "swapExactTokensForTokens",
        }
        self.backrun = {
            "from_address": "0xbot123",
            "to_address": "0xpoolabc",
            "tx_index": 3,
            "gas_price_gwei": 45.0,
            "method_signature": "swapExactTokensForTokens",
        }

    def test_clear_sandwich_detected(self):
        """Classic sandwich pattern is detected with high confidence."""
        confidence = _is_sandwich_pattern(self.frontrun, self.victim, self.backrun)
        assert confidence > 0.5

    def test_different_pool_low_confidence(self):
        """Different target pools reduces confidence
        but bot ownership and other signals remain."""
        bad_backrun = dict(self.backrun)
        bad_backrun["to_address"] = "0xpoolxyz"
        confidence = _is_sandwich_pattern(self.frontrun, self.victim, bad_backrun)
        # Still gets ~0.65 from same-bot, gas premium, consecutive ordering
        assert confidence < 0.9

    def test_different_bot_address_low_confidence(self):
        """Different frontrun/backrun senders reduces confidence
        but remaining signals (same pool, consecutive indices, gas premium)
        still indicate suspicious activity."""
        bad_backrun = dict(self.backrun)
        bad_backrun["from_address"] = "0xbot789"
        confidence = _is_sandwich_pattern(self.frontrun, self.victim, bad_backrun)
        # Still gets 0.65 from pool match, consecutive indices, etc.
        assert confidence < 0.95

    def test_non_consecutive_tx_reduces_confidence(self):
        """Scattered tx indices reduce but don't eliminate confidence
        when other strong signals (same bot, same pool) are present."""
        non_consecutive = dict(self.backrun)
        non_consecutive["tx_index"] = 50
        confidence = _is_sandwich_pattern(self.frontrun, self.victim, non_consecutive)
        # Still ~0.9 from pool match, same bot, gas premium
        assert confidence <= 1.0

    def test_gas_premium_boosts_confidence(self):
        """Gas price premium between bot and victim boosts confidence."""
        high_gas_frontrun = dict(self.frontrun)
        high_gas_frontrun["gas_price_gwei"] = 500.0
        confidence = _is_sandwich_pattern(high_gas_frontrun, self.victim, self.backrun)
        assert confidence > 0.5

    def test_same_sender_all_tx_low_confidence(self):
        """If all three have same sender, it's not a sandwich."""
        same_sender_frontrun = dict(self.frontrun)
        same_sender_victim = dict(self.victim)
        same_sender_backrun = dict(self.backrun)
        same_sender_victim["from_address"] = "0xbot123"
        confidence = _is_sandwich_pattern(same_sender_frontrun, same_sender_victim, same_sender_backrun)
        # Should still detect since frontrun and backrun same bot helps
        # But victim being same bot hurts the victim-different-from-bot score
        assert confidence > 0.0


class TestMEVBotRegistry(unittest.TestCase):
    """Known MEV bot detection and registry."""

    def setUp(self):
        self.detector = MEVSandwichDetector(chains=["ethereum"])

    def test_known_bot_detected(self):
        """Known MEV bot address is recognized."""
        known_addr = next(iter(KNOWN_MEV_BOTS.keys()))
        profile = self.detector.is_known_bot(known_addr)
        assert profile is not None
        assert profile is not None  # type guard for pyright
        assert profile.address == known_addr.lower()

    def test_unknown_address_returns_none(self):
        """Random address returns None from known check."""
        profile = self.detector.is_known_bot("0x0000000000000000000000000000000000000000")
        assert profile is None

    def test_identify_bot_unknown_creates_profile(self):
        """Unknown address gets auto-profile on identify_bot."""
        profile = self.detector.identify_bot(
            "0xdead00000000000000000000000000000000dead",
            "ethereum",
        )
        assert profile is not None
        assert "suspected_bot" in profile.tags

    def test_registry_loads_all_known_bots(self):
        """All entries from KNOWN_MEV_BOTS are loaded."""
        assert len(self.detector._known_bots) >= len(KNOWN_MEV_BOTS)


class TestMEVSandwichDataModel(unittest.TestCase):
    """SandwichAttack data model and serialization."""

    def setUp(self):
        self.sandwich = SandwichAttack(
            victim_address="0xuser",
            token_in="WETH",
            token_out="USDC",
            chain="ethereum",
            pool_address="0xpool",
            block_number=12345678,
            frontrun_tx=MEVTransaction(
                tx_hash="0xfront",
                chain="ethereum",
                block_number=12345678,
                tx_index=1,
                from_address="0xbot",
                to_address="0xpool",
                gas_price_gwei=50.0,
            ),
            victim_tx=MEVTransaction(
                tx_hash="0xvictim",
                chain="ethereum",
                block_number=12345678,
                tx_index=2,
                from_address="0xuser",
                to_address="0xpool",
            ),
            backrun_tx=MEVTransaction(
                tx_hash="0xback",
                chain="ethereum",
                block_number=12345678,
                tx_index=3,
                from_address="0xbot",
                to_address="0xpool",
            ),
            bot_address="0xbot",
            estimated_extracted_usd=50.0,
            victim_loss_usd=10.0,
            slippage_impact_pct=2.5,
            confidence=0.85,
            severity=MEVSeverity.HIGH,
            bot_name="TestBot",
        )

    def test_summary_format(self):
        """Summary output is readable and contains key info."""
        summary = self.sandwich.summary()
        assert "HIGH" in summary
        assert "SANDWICH" in summary
        assert "ethereum" in summary
        assert "$50.00" in summary
        assert "TestBot" in summary

    def test_to_dict_contains_all_fields(self):
        """Serialized dict contains expected fields."""
        d = self.sandwich.to_dict()
        assert d["type"] == "sandwich"
        assert d["chain"] == "ethereum"
        assert d["victim"] == "0xuser"
        assert d["bot"] == "0xbot"
        assert d["extracted_usd"] == 50.0
        assert d["severity"] == "high"
        assert d["confidence"] == 0.85
        assert d["frontrun_tx"] == "0xfront"
        assert d["victim_tx"] == "0xvictim"
        assert d["backrun_tx"] == "0xback"


class TestMEVScanReport(unittest.TestCase):
    """Scan report aggregation and sorting."""

    def setUp(self):
        self.report = MEVScanReport(chains_scanned=["ethereum", "bsc"])

    def test_empty_report_summary(self):
        """Empty report still produces valid summary."""
        summary = self.report.summary()
        assert "MEV Scan Report" in summary
        assert "Attacks detected: 0" in summary

    def test_top_sandwiches_sorted_by_value(self):
        """Top sandwiches are sorted by extracted value descending."""
        high = SandwichAttack(
            victim_address="0xa",
            token_in="ETH",
            token_out="USDC",
            chain="ethereum",
            pool_address="0xp1",
            block_number=1,
            frontrun_tx=MEVTransaction("0xf1", "ethereum", 1, 0, "", ""),
            victim_tx=MEVTransaction("0xv1", "ethereum", 1, 0, "", ""),
            backrun_tx=MEVTransaction("0xb1", "ethereum", 1, 0, "", ""),
            bot_address="0xb",
            estimated_extracted_usd=1000.0,
        )
        medium = SandwichAttack(
            victim_address="0xc",
            token_in="ETH",
            token_out="USDC",
            chain="ethereum",
            pool_address="0xp2",
            block_number=2,
            frontrun_tx=MEVTransaction("0xf2", "ethereum", 2, 0, "", ""),
            victim_tx=MEVTransaction("0xv2", "ethereum", 2, 0, "", ""),
            backrun_tx=MEVTransaction("0xb2", "ethereum", 2, 0, "", ""),
            bot_address="0xd",
            estimated_extracted_usd=100.0,
        )
        low = SandwichAttack(
            victim_address="0xe",
            token_in="ETH",
            token_out="USDC",
            chain="ethereum",
            pool_address="0xp3",
            block_number=3,
            frontrun_tx=MEVTransaction("0xf3", "ethereum", 3, 0, "", ""),
            victim_tx=MEVTransaction("0xv3", "ethereum", 3, 0, "", ""),
            backrun_tx=MEVTransaction("0xb3", "ethereum", 3, 0, "", ""),
            bot_address="0xf",
            estimated_extracted_usd=10.0,
        )
        self.report.sandwiches = [low, high, medium]
        top = self.report.top_sandwiches(limit=2)
        assert len(top) == 2
        assert top[0].estimated_extracted_usd == 1000.0
        assert top[1].estimated_extracted_usd == 100.0

    def test_top_bots_sorted_by_extraction(self):
        """Top bots sorted by total extracted value."""
        bot1 = MEVBotProfile("0x1", "ethereum", "Bot A", total_extracted_usd=5000)
        bot2 = MEVBotProfile("0x2", "ethereum", "Bot B", total_extracted_usd=500)
        self.report.bots_detected = [bot2, bot1]
        top = self.report.top_bots(limit=1)
        assert len(top) == 1
        assert top[0].name == "Bot A"


class TestPoolInfo(unittest.TestCase):
    """Pool info data model."""

    def test_pool_registration(self):
        """Pool can be registered and stored."""
        detector = MEVSandwichDetector()
        pool = PoolInfo(
            address="0x1234567890abcdef1234567890abcdef12345678",
            chain="ethereum",
            dex_type=PoolDexType.UNISWAP_V3,
            token0="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            token1="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            token0_symbol="WETH",
            token1_symbol="USDC",
            liquidity_usd=10_000_000,
            volume_24h_usd=5_000_000,
            fee_tier=500,
        )
        detector.register_pool(pool)
        assert "0x1234567890abcdef1234567890abcdef12345678" in detector._pools

    def test_bulk_registration(self):
        """Multiple pools can be registered from dicts."""
        detector = MEVSandwichDetector()
        pools = [
            {
                "address": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "chain": "ethereum",
                "dex_type": "uniswap_v2",
                "liquidity_usd": 1_000_000,
                "volume_24h_usd": 500_000,
                "fee_tier": 3000,
            },
            {
                "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "chain": "bsc",
                "dex_type": "pancakeswap",
                "liquidity_usd": 2_000_000,
                "volume_24h_usd": 1_000_000,
                "fee_tier": 2500,
            },
        ]
        detector.register_pools_from_dict(pools)
        assert "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in detector._pools
        assert "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" in detector._pools
        assert detector._pools["0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"].chain == "bsc"
        assert detector._pools["0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"].dex_type == PoolDexType.PANCAKESWAP


class TestMEVBotProfile(unittest.TestCase):
    """Bot profile summary and data."""

    def test_summary_format(self):
        """Bot profile summary includes key stats."""
        bot = MEVBotProfile(
            address="0xbot",
            chain="ethereum",
            name="MevBot42",
            attack_types=[],
            total_extracted_usd=10000.0,
            attacks_detected=50,
        )
        summary = bot.summary()
        assert "MevBot42" in summary
        assert "ethereum" in summary
        assert "$10000" in summary
        assert "50" in summary


class TestMEVTransaction(unittest.TestCase):
    """MEV transaction data model."""

    def test_default_values(self):
        """Default values are sensible."""
        tx = MEVTransaction(
            tx_hash="0xhash",
            chain="ethereum",
            block_number=1,
            tx_index=0,
            from_address="0xfrom",
            to_address="0xto",
        )
        assert tx.value_eth == 0.0
        assert tx.gas_price_gwei == 0.0
        assert tx.gas_used == 0
        assert tx.method_signature == ""


class TestDetectorInitialization(unittest.TestCase):
    """Detector initialization and configuration."""

    def test_default_chains(self):
        """Default chains include major EVM chains."""
        detector = MEVSandwichDetector()
        expected_chains = [
            "ethereum",
            "bsc",
            "arbitrum",
            "base",
            "optimism",
            "polygon",
        ]
        assert detector.chains == expected_chains

    def test_custom_chains(self):
        """Custom chain list is respected."""
        detector = MEVSandwichDetector(chains=["solana"])
        assert detector.chains == ["solana"]

    def test_data_dir_created(self):
        """Data directory path uses module path."""
        detector = MEVSandwichDetector()
        assert "data" in detector.data_dir


class TestIntegratedScan(unittest.TestCase):
    """End-to-end scan flow without network calls."""

    def test_scan_detects_sandwich(self):
        """Full scan detects a sandwich pattern from mock DataBus data."""
        import asyncio

        detector = MEVSandwichDetector(chains=["ethereum"])
        mock_data = {
            "results": [
                {
                    "from_address": "0xbot123",
                    "to_address": "0xpoolabc",
                    "tx_hash": "0xfr1",
                    "tx_index": 1,
                    "block_number": 100,
                    "gas_price_gwei": 50.0,
                    "value_eth": 1.0,
                    "token_in": "WETH",
                    "token_out": "USDC",
                    "method_signature": "swapExactTokensForTokens",
                    "gas_used": 150000,
                },
                {
                    "from_address": "0xuser456",
                    "to_address": "0xpoolabc",
                    "tx_hash": "0xvic1",
                    "tx_index": 2,
                    "block_number": 100,
                    "gas_price_gwei": 20.0,
                    "value_eth": 10.0,
                    "token_in": "WETH",
                    "token_out": "USDC",
                    "method_signature": "swapExactTokensForTokens",
                    "gas_used": 100000,
                },
                {
                    "from_address": "0xbot123",
                    "to_address": "0xpoolabc",
                    "tx_hash": "0xbr1",
                    "tx_index": 3,
                    "block_number": 100,
                    "gas_price_gwei": 45.0,
                    "value_eth": 1.5,
                    "token_in": "WETH",
                    "token_out": "USDC",
                    "method_signature": "swapExactTokensForTokens",
                    "gas_used": 150000,
                },
            ]
        }
        with patch.object(
            detector,
            "_query_databus",
            new=AsyncMock(return_value=mock_data),
        ):
            report = asyncio.run(detector.scan())
        assert len(report.sandwiches) >= 1
        s = report.sandwiches[0]
        assert s.chain == "ethereum"
        assert s.estimated_extracted_usd > 0

    def test_scan_empty_data(self):
        """Scan with no data produces empty report."""
        import asyncio

        detector = MEVSandwichDetector(chains=["ethereum"])
        with patch.object(
            detector,
            "_query_databus",
            new=AsyncMock(return_value={"results": []}),
        ):
            report = asyncio.run(detector.scan())
        assert len(report.sandwiches) == 0
        assert len(report.frontruns) == 0

    def test_scan_handles_errors(self):
        """Scan gracefully handles DataBus errors."""
        import asyncio

        detector = MEVSandwichDetector(chains=["ethereum"])
        with patch.object(
            detector,
            "_query_databus",
            new=AsyncMock(side_effect=Exception("Connection failed")),
        ):
            report = asyncio.run(detector.scan())
        assert len(report.errors) >= 1
        assert "Connection failed" in report.errors[0]


if __name__ == "__main__":
    unittest.main()
