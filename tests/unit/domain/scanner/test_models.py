"""Tests for app/domains/scanner/models.py - ScanResult dataclass and CHAIN_IDS.

Tests the data models used throughout the scanner pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.scanner.models import CHAIN_IDS, SOLANA_RPC_ENDPOINTS, ScanResult


class TestScanResult:
    """Tests for ScanResult dataclass."""

    def test_scan_result_defaults(self):
        """ScanResult should have sensible defaults."""
        result = ScanResult(token_address="0xabc", chain="ethereum")
        assert result.token_address == "0xabc"
        assert result.chain == "ethereum"
        assert result.symbol == ""
        assert result.name == ""
        assert result.safety_score == 50  # default = unknown
        assert result.confidence == 0
        assert result.tier_required == "free"
        assert result.risk_flags == []
        assert result.modules_run == []

    def test_scan_result_with_all_fields(self):
        """ScanResult should accept all fields."""
        now = datetime.now(UTC).isoformat()
        result = ScanResult(
            token_address="0xdef",
            chain="solana",
            symbol="SOL",
            name="Solana Token",
            scanned_at=now,
            safety_score=85,
            risk_flags=["honeypot", "high_concentration"],
            tier_required="pro",
            confidence=75,
            free={"price": 1.5},
            modules_run=[{"module": "test", "status": "success"}],
        )
        assert result.token_address == "0xdef"
        assert result.symbol == "SOL"
        assert result.safety_score == 85
        assert len(result.risk_flags) == 2
        assert result.tier_required == "pro"

    def test_scan_result_scanned_at_auto_generated(self):
        """scanned_at should be auto-generated if not provided."""
        result = ScanResult(token_address="0xaaa", chain="ethereum")
        assert result.scanned_at is not None
        # Should be a valid ISO datetime
        datetime.fromisoformat(result.scanned_at)

    def test_scan_result_mutable_fields(self):
        """List fields should be mutable after creation."""
        result = ScanResult(token_address="0xbbb", chain="base")
        result.risk_flags.append("low_liquidity")
        result.modules_run.append({"module": "honeypot", "status": "success"})
        assert len(result.risk_flags) == 1
        assert len(result.modules_run) == 1

    def test_scan_result_free_pro_elite_storage(self):
        """Result should store tier-specific data separately."""
        result = ScanResult(token_address="0xccc", chain="arbitrum")
        result.free["price"] = 10.0
        result.pro["holders"] = 1000
        result.elite["deep_analysis"] = {"risk": "low"}
        assert result.free["price"] == 10.0
        assert result.pro["holders"] == 1000
        assert result.elite["deep_analysis"]["risk"] == "low"


class TestChainIDs:
    """Tests for CHAIN_IDS mapping."""

    def test_chain_ids_contains_major_chains(self):
        """CHAIN_IDS should contain all major supported chains."""
        major_chains = ["solana", "ethereum", "base", "bsc", "arbitrum", "polygon"]
        for chain in major_chains:
            assert chain in CHAIN_IDS, f"Missing chain: {chain}"

    def test_chain_ids_values_are_strings(self):
        """All chain IDs should be strings."""
        for chain, cid in CHAIN_IDS.items():
            assert isinstance(cid, str), f"{chain} has non-string ID: {cid}"

    def test_ethereum_chain_id_is_1(self):
        """Ethereum should map to chain ID '1'."""
        assert CHAIN_IDS["ethereum"] == "1"

    def test_solana_chain_id(self):
        """Solana should be 'solana' (not EVM)."""
        assert CHAIN_IDS["solana"] == "solana"

    def test_all_chains_unique(self):
        """All chain ID values should be unique."""
        values = list(CHAIN_IDS.values())
        assert len(values) == len(set(values))


class TestSolanaRPCEndpoints:
    """Tests for SOLANA_RPC_ENDPOINTS."""

    def test_contains_endpoints(self):
        """Should have at least one RPC endpoint."""
        assert len(SOLANA_RPC_ENDPOINTS) >= 1

    def test_endpoints_are_urls(self):
        """All endpoints should be valid URLs."""
        for endpoint in SOLANA_RPC_ENDPOINTS:
            assert endpoint.startswith(("https://", "http://"))
