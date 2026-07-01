"""
Tests for Launch Fairness Analyzer.
"""

import unittest

from app.launch_fairness_analyzer import (
    FairnessSignal,
    FairnessSignalResult,
    LaunchFairnessResult,
    Severity,
    _detect_bot_activity,
    _detect_bundled_launch,
    _detect_concentrated_holders,
    _detect_lp_manipulation,
    _detect_presale_concentration,
    _detect_rapid_dump,
    _detect_sniped_distribution,
    _risk_level_from_score,
    _severity_from_score,
    analyze_launch_fairness,
)


class TestLaunchFairnessAnalyzer(unittest.TestCase):
    """Test the main analyze_launch_fairness function."""

    def test_basic_analysis(self) -> None:
        """Should produce a valid fairness analysis from a token address."""
        import asyncio

        result = asyncio.run(
            analyze_launch_fairness(
                "0x1234567890abcdef1234567890abcdef12345678",
                chain="ethereum",
                simulate_data=True,
            )
        )
        self.assertIn("token_address", result)
        self.assertEqual(
            result["token_address"],
            "0x1234567890abcdef1234567890abcdef12345678",
        )
        self.assertIn("fairness_score", result)
        self.assertIn("risk_level", result)
        self.assertIn("signals", result)
        self.assertIn("summary", result)

    def test_solana_address(self) -> None:
        """Solana address format should be detected."""
        import asyncio

        sol_addr = "AbCdEf1234567890AbCdEf1234567890AbCdEf1234567890AbCdEf1234567890"
        result = asyncio.run(analyze_launch_fairness(sol_addr, chain="auto", simulate_data=True))
        self.assertIn("solana", result["chain"])

    def test_invalid_address(self) -> None:
        """Invalid address should produce warnings but not crash."""
        import asyncio

        result = asyncio.run(analyze_launch_fairness("invalid!", chain="ethereum", simulate_data=True))
        self.assertIn("warnings", result)
        self.assertTrue(len(result["warnings"]) > 0)

    def test_analysis_has_signals(self) -> None:
        """Analysis should return all signal types."""
        import asyncio

        result = asyncio.run(
            analyze_launch_fairness(
                "0xabcdef1234567890abcdef1234567890abcdef12",
                chain="ethereum",
                simulate_data=True,
            )
        )
        signal_names = {s["signal"] for s in result["signals"]}
        expected_signals = {
            "sniped_distribution",
            "bundled_launch",
            "concentrated_top_holders",
            "lp_manipulation",
            "bot_activity",
            "presale_concentration",
            "rapid_dump_signal",
        }
        self.assertEqual(signal_names, expected_signals)

    def test_analysis_time_is_measured(self) -> None:
        """Analysis time should be a positive number."""
        import asyncio

        result = asyncio.run(
            analyze_launch_fairness(
                "0xdead000000000000000000000000000000000000",
                chain="ethereum",
                simulate_data=True,
            )
        )
        self.assertGreater(result["analysis_time_ms"], 0)


class TestSignalDetection(unittest.TestCase):
    """Test individual signal detectors."""

    def test_sniped_detection_no_data(self) -> None:
        """No data should return undetected."""
        result = _detect_sniped_distribution("0xabc", "ethereum")
        self.assertFalse(result.detected)
        self.assertIn("No transaction data", result.details)

    def test_sniped_detection_with_data(self) -> None:
        """Multiple same-block buys should trigger sniper detection."""
        txs = [
            {
                "from": f"0x{i:040x}",
                "to": "0xtoken",
                "block_number": 1,
                "amount_usd": 1000,
                "type": "buy",
            }
            for i in range(5)
        ]
        result = _detect_sniped_distribution("0xabc", "ethereum", txs)
        self.assertTrue(result.detected)
        self.assertGreaterEqual(result.score, 0.4)

    def test_bundle_detection_no_data(self) -> None:
        """No data should return undetected."""
        result = _detect_bundled_launch("0xabc", "ethereum", [])
        self.assertFalse(result.detected)
        self.assertIn("Insufficient transaction data", result.details)

    def test_bundle_detection_with_funders(self) -> None:
        """Multiple wallets funded by same source should trigger bundling."""
        txs = [
            {
                "from": f"0x{i:040x}",
                "to": "0xtoken",
                "funded_by": "0xfunder123",
                "amount_usd": 5000,
                "block_number": 1,
            }
            for i in range(5)
        ]
        result = _detect_bundled_launch("0xabc", "ethereum", txs)
        self.assertTrue(result.detected)
        self.assertGreaterEqual(result.score, 0.2)

    def test_concentration_high(self) -> None:
        """90%+ concentration should be critical."""
        holders = [{"address": f"0x{i:040x}", "balance": 100_000_000} for i in range(3)]
        holders.append({"address": "0xsmall", "balance": 1_000_000})
        result = _detect_concentrated_holders(holders)
        # Top 3 hold 300M out of 301M = ~99.7%
        self.assertTrue(result.detected)
        self.assertEqual(result.severity, Severity.CRITICAL)

    def test_concentration_low(self) -> None:
        """Low concentration should not be flagged."""
        holders = [{"address": f"0x{i:040x}", "balance": 1_000_000} for i in range(100)]
        result = _detect_concentrated_holders(holders)
        # Top 10 hold 10M out of 100M = 10%
        self.assertFalse(result.detected)

    def test_concentration_no_data(self) -> None:
        """No holder data should return undetected."""
        result = _detect_concentrated_holders([])
        self.assertFalse(result.detected)

    def test_lp_manipulation_delayed(self) -> None:
        """Delayed LP addition should be flagged."""
        txs = [
            {
                "from": "0xbuyer",
                "to": "0xtoken",
                "block_number": 5,
                "type": "buy",
                "amount_usd": 100,
            }
        ]
        lp_data = {"add_delay_blocks": 500, "lp_token_concentration": 0.0}
        result = _detect_lp_manipulation(lp_data, txs)
        self.assertTrue(result.detected)
        self.assertGreaterEqual(result.score, 0.4)

    def test_lp_manipulation_removed(self) -> None:
        """LP removed should be flagged as high severity."""
        txs = [
            {
                "from": "0xevil",
                "to": "0xtoken",
                "type": "remove_liquidity",
                "block_number": 10,
                "amount_usd": 50000,
            }
        ]
        result = _detect_lp_manipulation({}, txs)
        self.assertTrue(result.detected)
        self.assertGreaterEqual(result.score, 0.4)

    def test_bot_detection_high_tx(self) -> None:
        """High transaction count from same wallet should be bot flagged."""
        txs = [
            {
                "from": "0xbotwallet",
                "to": "0xother",
                "type": "swap",
                "amount_usd": 100,
                "timestamp": 1700000000 + i,
                "gas_price_gwei": 50,
            }
            for i in range(10)
        ]
        result = _detect_bot_activity(txs)
        self.assertTrue(result.detected)

    def test_bot_detection_no_data(self) -> None:
        """No data should return undetected."""
        result = _detect_bot_activity([])
        self.assertFalse(result.detected)
        self.assertIn("Insufficient transaction data", result.details)

    def test_presale_concentration_high(self) -> None:
        """50%+ presale should be critical."""
        presale = {
            "presale_allocation_pct": 60.0,
            "participant_count": 100,
            "insider_allocation_pct": 5.0,
            "vc_allocation_pct": 10.0,
        }
        result = _detect_presale_concentration(presale)
        self.assertTrue(result.detected)
        self.assertEqual(result.severity, Severity.CRITICAL)

    def test_presale_concentration_missing(self) -> None:
        """No presale data should return undetected."""
        result = _detect_presale_concentration(None)
        self.assertFalse(result.detected)

    def test_rapid_dump_no_sells(self) -> None:
        """No early sells should return undetected."""
        txs = [
            {
                "from": "0xbuyer",
                "to": "0xtoken",
                "type": "buy",
                "block_number": 1,
                "amount_usd": 1000,
            }
        ]
        result = _detect_rapid_dump(txs)
        self.assertFalse(result.detected)

    def test_rapid_dump_detected(self) -> None:
        """Large early sells should be flagged."""
        txs = [
            {
                "from": f"0x{i:040x}",
                "to": "0xtoken",
                "type": "sell",
                "block_number": 3,
                "amount_usd": 50000,
            }
            for i in range(3)
        ]
        result = _detect_rapid_dump(txs)
        self.assertTrue(result.detected)
        self.assertGreaterEqual(result.score, 0.6)


class TestScoring(unittest.TestCase):
    """Test scoring utilities."""

    def test_severity_mapping(self) -> None:
        self.assertEqual(_severity_from_score(0.9), Severity.CRITICAL)
        self.assertEqual(_severity_from_score(0.7), Severity.HIGH)
        self.assertEqual(_severity_from_score(0.5), Severity.MODERATE)
        self.assertEqual(_severity_from_score(0.3), Severity.LOW)
        self.assertEqual(_severity_from_score(0.1), Severity.NONE)

    def test_risk_level_mapping(self) -> None:
        self.assertEqual(_risk_level_from_score(90), "low")
        self.assertEqual(_risk_level_from_score(70), "medium")
        self.assertEqual(_risk_level_from_score(50), "high")
        self.assertEqual(_risk_level_from_score(30), "critical")


class TestSerialization(unittest.TestCase):
    """Test serialization of result objects."""

    def test_fairness_signal_result_to_dict(self) -> None:
        signal = FairnessSignalResult(
            signal=FairnessSignal.SNIPED_DISTRIBUTION,
            detected=True,
            severity=Severity.HIGH,
            score=0.75,
            details="Sniping detected",
            evidence=["Block 1: 5 wallets bought"],
        )
        d = signal.to_dict()
        self.assertEqual(d["signal"], "sniped_distribution")
        self.assertTrue(d["detected"])
        self.assertEqual(d["severity"], "high")
        self.assertEqual(d["score"], 0.75)

    def test_launch_fairness_result_to_dict(self) -> None:
        result = LaunchFairnessResult(token_address="0xabc", chain="ethereum")
        result.fairness_score = 45.0
        result.risk_level = "high"
        result.summary = "High risk — multiple signals"
        result.signals = [
            FairnessSignalResult(
                signal=FairnessSignal.BUNDLED_LAUNCH,
                detected=True,
                score=0.6,
            )
        ]
        d = result.to_dict()
        self.assertEqual(d["token_address"], "0xabc")
        self.assertEqual(d["chain"], "ethereum")
        self.assertEqual(d["fairness_score"], 45.0)
        self.assertEqual(d["risk_level"], "high")
        self.assertEqual(len(d["signals"]), 1)

    def test_empty_result_serialization(self) -> None:
        result = LaunchFairnessResult(token_address="0xempty", chain="ethereum")
        d = result.to_dict()
        self.assertEqual(d["token_address"], "0xempty")
        self.assertEqual(d["fairness_score"], 100.0)
        self.assertIn("summary", d)
        self.assertIn("analysis_time_ms", d)


if __name__ == "__main__":
    unittest.main()
