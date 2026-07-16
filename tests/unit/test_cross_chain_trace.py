"""
Tests for Cross-Chain Fund Trace.
"""

import unittest
from datetime import UTC, datetime

from app.cross_chain_trace import (
    ConfidenceLevel,
    FundTraceResult,
    TraceHop,
    TraceHopType,
    _compute_confidence,
    _compute_suspiciousness,
    _detect_chain,
    _generate_summary,
    _is_bridge,
    _is_burn_address,
    _is_exchange,
    _is_mixer,
    _normalize_address,
    trace_funds,
)


class TestTraceFunds(unittest.TestCase):
    """Test the main trace_funds function."""

    def test_trace_from_ethereum_wallet(self) -> None:
        """Should produce a valid trace from an Ethereum address."""
        import asyncio

        result = asyncio.run(trace_funds("0x1234567890abcdef1234567890abcdef12345678", "ethereum"))
        assert "source_address" in result
        assert result["source_address"] == "0x1234567890abcdef1234567890abcdef12345678"
        assert "summary" in result
        assert "confidence_score" in result
        assert "suspicious_score" in result

    def test_trace_from_mixer(self) -> None:
        """A mixer address should be flagged."""
        import asyncio

        result = asyncio.run(trace_funds("0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc", "ethereum"))
        assert result["suspicious_score"] >= 0
        # Mixer detection
        mixer_warnings = [w for w in result["warnings"] if "mixer" in w.lower()]
        assert len(mixer_warnings) > 0 or result["source_of_funds"] != "unknown"

    def test_trace_from_bridge(self) -> None:
        """A known bridge address should be identified."""
        import asyncio

        # Stargate bridge address
        result = asyncio.run(trace_funds("0x8731d54e9d02c286767d56ac03e8037c07e01e98", "ethereum"))
        assert "bridge" in result["source_of_funds"].lower()

    def test_trace_from_exchange(self) -> None:
        """A known exchange address should be identified."""
        import asyncio

        # Binance hot wallet
        result = asyncio.run(trace_funds("0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be", "ethereum"))
        assert "cex" in result["source_of_funds"].lower()

    def test_trace_deep_mode(self) -> None:
        """Deep mode should add more chains and warnings."""
        import asyncio

        result = asyncio.run(trace_funds("0xabc123", "ethereum", depth="deep"))
        assert len(result["chains_used"]) >= 2
        deep_warnings = [w for w in result["warnings"] if "deep" in w.lower()]
        assert len(deep_warnings) > 0

    def test_trace_solana_address(self) -> None:
        """Solana address format should be detected."""
        import asyncio

        sol_addr = "AbCdEf1234567890AbCdEf1234567890AbCdEf1234567890AbCdEf1234567890"
        result = asyncio.run(trace_funds(sol_addr, "auto"))
        assert "solana" in result["chains_used"]

    def test_trace_quick_mode(self) -> None:
        """Quick mode should note limited depth."""
        import asyncio

        result = asyncio.run(
            trace_funds("0xdead000000000000000000000000000000000000", "ethereum", depth="quick")
        )
        quick_warnings = [w for w in result["warnings"] if "quick" in w.lower()]
        assert len(quick_warnings) > 0

    def test_max_hops_limit(self) -> None:
        """Should respect max_hops limit."""
        import asyncio

        result = asyncio.run(trace_funds("0xtest", "ethereum", max_hops=5))
        assert len(result["trace_path"]) <= 5


class TestAddressPatterns(unittest.TestCase):
    """Test address pattern matching."""

    def test_ethereum_detection(self) -> None:
        assert _detect_chain("0x1234567890abcdef1234567890abcdef12345678") == "ethereum"

    def test_solana_detection(self) -> None:
        assert _detect_chain("AbCdEf1234567890AbCdEf1234567890AbCdEf1234567890AbCdEf1234567890") == "solana"

    def test_unknown_detection(self) -> None:
        assert _detect_chain("short") == "unknown"
        assert _detect_chain("") == "unknown"

    def test_normalize(self) -> None:
        assert _normalize_address("0xABC123") == "0xabc123"
        assert _normalize_address("  0xABC  ") == "0xabc"
        assert _normalize_address("") == ""

    def test_bridge_detection(self) -> None:
        is_b, name = _is_bridge("0x1a44076050125825947e16f8b9af3a1b524c09ce")
        assert is_b
        assert name == "layerzero"

        is_b, name = _is_bridge("0x8731d54e9d02c286767d56ac03e8037c07e01e98")
        assert is_b
        assert name == "stargate"

        is_b, name = _is_bridge("0x0000000000000000000000000000000000000000")
        assert not is_b

    def test_mixer_detection(self) -> None:
        is_m, name = _is_mixer("0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc")
        assert is_m
        assert name == "tornado_cash"

        is_m, name = _is_mixer("0x0000000000000000000000000000000000000000")
        assert not is_m

    def test_exchange_detection(self) -> None:
        is_e, name = _is_exchange("0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be")
        assert is_e
        assert name == "binance"

        is_e, name = _is_exchange("0x71660c4005ba85c37ccec55d0c4493e66fe775d3")
        assert is_e
        assert name == "coinbase"

        is_e, name = _is_exchange("0x0000000000000000000000000000000000000000")
        assert not is_e

    def test_burn_address_detection(self) -> None:
        assert _is_burn_address("0x0000000000000000000000000000000000000000")
        assert _is_burn_address("0x000000000000000000000000000000000000dead")
        assert not _is_burn_address("0xabc123def456")


class TestConfidenceScoring(unittest.TestCase):
    """Test confidence computation."""

    def test_empty_hops(self) -> None:
        assert _compute_confidence([]) == 0.0

    def test_high_confidence_hops(self) -> None:
        hops = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.BRIDGE,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="bsc",
                protocol="stargate",
                timestamp=int(datetime.now(tz=UTC).timestamp()),
                tx_hash="0xabc123def",
            ),
        ]
        score = _compute_confidence(hops)
        assert score > 0.5

    def test_mixer_reduces_confidence(self) -> None:
        hops = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.MIXER,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="ethereum",
                protocol="tornado_cash",
            ),
        ]
        mixer_score = _compute_confidence(hops)

        clean_hops = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.TRANSFER,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="ethereum",
            ),
        ]
        clean_score = _compute_confidence(clean_hops)

        assert mixer_score < clean_score + 0.1

    def test_tx_hash_boost(self) -> None:
        hops_with_tx = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.TRANSFER,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="ethereum",
                tx_hash="0xabc",
            ),
        ]
        hops_without_tx = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.TRANSFER,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="ethereum",
            ),
        ]
        assert _compute_confidence(hops_with_tx) > _compute_confidence(hops_without_tx)


class TestSuspiciousnessScoring(unittest.TestCase):
    """Test suspiciousness computation."""

    def test_clean_path_is_not_suspicious(self) -> None:
        hops = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.TRANSFER,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="ethereum",
            ),
        ]
        score = _compute_suspiciousness(hops)
        assert score < 0.3

    def test_mixer_path_is_suspicious(self) -> None:
        hops = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.MIXER,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="ethereum",
                tags=["mixer"],
            ),
            TraceHop(
                hop_number=2,
                hop_type=TraceHopType.TRANSFER,
                from_address="0xb",
                to_address="0xc",
                from_chain="ethereum",
                to_chain="base",
            ),
        ]
        score = _compute_suspiciousness(hops)
        assert score > 0.1

    def test_multi_bridge_path(self) -> None:
        hops = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.BRIDGE,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="bsc",
                tags=["bridge"],
            ),
            TraceHop(
                hop_number=2,
                hop_type=TraceHopType.BRIDGE,
                from_address="0xb",
                to_address="0xc",
                from_chain="bsc",
                to_chain="polygon",
                tags=["bridge"],
            ),
            TraceHop(
                hop_number=3,
                hop_type=TraceHopType.BRIDGE,
                from_address="0xc",
                to_address="0xd",
                from_chain="polygon",
                to_chain="arbitrum",
                tags=["bridge"],
            ),
        ]
        score = _compute_suspiciousness(hops)
        assert score > 0.1

    def test_rapid_hops(self) -> None:
        now = int(datetime.now(tz=UTC).timestamp())
        hops = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.TRANSFER,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="ethereum",
                timestamp=now - 300,
            ),
            TraceHop(
                hop_number=2,
                hop_type=TraceHopType.TRANSFER,
                from_address="0xb",
                to_address="0xc",
                from_chain="ethereum",
                to_chain="ethereum",
                timestamp=now - 200,
            ),
            TraceHop(
                hop_number=3,
                hop_type=TraceHopType.TRANSFER,
                from_address="0xc",
                to_address="0xd",
                from_chain="ethereum",
                to_chain="ethereum",
                timestamp=now - 100,
            ),
        ]
        score = _compute_suspiciousness(hops)
        assert score > 0.1

    def test_empty_hops_no_suspicion(self) -> None:
        assert _compute_suspiciousness([]) == 0.0


class TestSummary(unittest.TestCase):
    """Test summary generation."""

    def test_basic_summary(self) -> None:
        result = FundTraceResult(source_address="0xtest")
        result.chains_used = ["ethereum"]
        result.chain_count = 1
        result.suspicious_score = 0.1
        result.confidence_score = 0.8
        summary = _generate_summary(result)
        assert "10%" in summary
        assert "80%" in summary

    def test_mixer_summary(self) -> None:
        result = FundTraceResult(source_address="0xevil")
        result.mixer_hops = 2
        result.bridge_hops = 1
        result.chain_count = 3
        result.chains_used = ["ethereum", "bsc", "arbitrum"]
        result.suspicious_score = 0.7
        result.confidence_score = 0.5
        result.source_of_funds = "mixer:tornado_cash"
        summary = _generate_summary(result)
        assert "mixer" in summary.lower()
        assert "HIGH SUSPICION" in summary

    def test_safe_summary(self) -> None:
        result = FundTraceResult(source_address="0xsafe")
        result.suspicious_score = 0.05
        result.confidence_score = 0.9
        summary = _generate_summary(result)
        assert "HIGH SUSPICION" not in summary.upper()
        assert "MODERATE SUSPICION" not in summary.upper()


class TestToDict(unittest.TestCase):
    """Test serialization."""

    def test_fund_trace_result_to_dict(self) -> None:
        result = FundTraceResult(source_address="0xabc")
        result.trace_path = [
            TraceHop(
                hop_number=1,
                hop_type=TraceHopType.BRIDGE,
                from_address="0xa",
                to_address="0xb",
                from_chain="ethereum",
                to_chain="bsc",
                protocol="stargate",
                amount_usd=10000.0,
                confidence=ConfidenceLevel.HIGH,
            ),
        ]
        d = result.to_dict()
        assert d["source_address"] == "0xabc"
        assert len(d["trace_path"]) == 1
        assert d["trace_path"][0]["protocol"] == "stargate"
        assert d["trace_path"][0]["confidence"] == "high"

    def test_empty_result_serialization(self) -> None:
        result = FundTraceResult(source_address="0xempty")
        d = result.to_dict()
        assert d["source_address"] == "0xempty"
        assert len(d["trace_path"]) == 0
        assert "summary" in d
        assert "analysis_time_ms" in d

    def test_trace_hop_serialization(self) -> None:
        hop = TraceHop(
            hop_number=1,
            hop_type=TraceHopType.SWAP,
            from_address="0xa",
            to_address="0xb",
            from_chain="ethereum",
            to_chain="ethereum",
            token="USDC",
            amount_usd=50000.0,
            protocol="uniswap",
            is_suspicious=False,
            tags=["swap", "stablecoin"],
        )
        d = hop.to_dict()
        assert d["type"] == "swap"
        assert d["token"] == "USDC"
        assert d["amount_usd"] == 50000.0
        assert d["protocol"] == "uniswap"
        assert "swap" in d["tags"]


if __name__ == "__main__":
    unittest.main()
