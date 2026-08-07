"""Unit tests for app/domains/scanners/cross_chain_trace/analyzer.py.

Tests cross-chain transaction tracing, fund-flow visualization data
generation (nodes/edges), bridge hop detection, entity flagging, and
graceful error handling. ClickHouse queries are mocked at the module
level — no database access.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanners.cross_chain_trace.analyzer import (
    CrossChainTraceAnalyzer,
)
from app.domains.scanners.cross_chain_trace.constants import _BRIDGE_CONTRACTS
from app.domains.scanners.cross_chain_trace.utils import (
    _assign_role,
    _evm_address_portable,
    _report_to_dict,
)

# Deterministic bridge contract address for the seed chain (ethereum).
ETH_BRIDGE = sorted(_BRIDGE_CONTRACTS["ethereum"])[0]

SEED = "0xseed" + "0" * 36
COUNTERPARTY = "0xcafe" + "0" * 36


def _transfer(
    chain: str,
    from_addr: str,
    to_addr: str,
    value: str = "1000000000000000000",  # 1 ETH wei
    tx_hash: str = "0xtx",
    token_symbol: str | None = "ETH",  # fixture param named by rule heuristic
) -> dict:
    """Build a transfer dict matching queries._fetch_transfers output."""
    return {
        "chain": chain,
        "block_number": 100,
        "tx_hash": tx_hash,
        "from_address": from_addr,
        "to_address": to_addr,
        "token_address": "0xtoken",
        "value": value,
        "block_timestamp": "2025-01-01T00:00:00",
        "token_symbol": token_symbol,
    }


class TestTraceFundFlow:
    """Node/edge graph generation from transfer data."""

    @pytest.mark.asyncio
    async def test_trace_single_chain_builds_nodes_and_edges(self) -> None:
        """Transfers should produce nodes, edges, and volume totals."""
        analyzer = CrossChainTraceAnalyzer()
        transfers = [_transfer("ethereum", SEED, COUNTERPARTY)]
        with (
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
                AsyncMock(side_effect=[transfers, [], [], []]),
            ),
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_labels",
                AsyncMock(return_value={}),
            ),
        ):
            report = await analyzer.analyze(SEED, "ethereum")

        assert report.seed_address == SEED
        assert len(report.nodes) == 2
        assert len(report.edges) == 1
        assert report.total_volume_sent == pytest.approx(1.0)
        assert report.total_volume_received == pytest.approx(1.0)
        assert report.unique_counterparties == 1

        node_by_addr = {n["address"]: n for n in report.nodes}
        assert node_by_addr[SEED]["role"] == "origin"
        assert node_by_addr[COUNTERPARTY]["role"] == "intermediate"
        assert node_by_addr[SEED]["total_sent"] == pytest.approx(1.0)

        edge = report.edges[0]
        assert edge["from_address"] == SEED
        assert edge["to_address"] == COUNTERPARTY
        assert edge["chain"] == "ethereum"
        assert edge["value"] == "1000000000000000000"

    @pytest.mark.asyncio
    async def test_trace_cross_chain_aggregates_chains(self) -> None:
        """Transfers on other EVM chains should be aggregated into the report."""
        analyzer = CrossChainTraceAnalyzer()
        eth_transfers = [_transfer("ethereum", SEED, COUNTERPARTY, tx_hash="0xa")]
        bsc_transfers = [_transfer("bsc", SEED, "0xother" + "0" * 36, value="2000000000000000000", tx_hash="0xb")]
        with (
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
                AsyncMock(side_effect=[eth_transfers, bsc_transfers, [], []]),
            ),
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_labels",
                AsyncMock(return_value={}),
            ),
        ):
            report = await analyzer.analyze(SEED, "ethereum")

        assert report.chains_involved == ["bsc", "ethereum"]
        assert report.total_volume_sent == pytest.approx(3.0)
        assert len(report.edges) == 2

    @pytest.mark.asyncio
    async def test_trace_deduplicates_repeated_edges(self) -> None:
        """Duplicate (from, to, chain, block) transfers should be deduplicated."""
        analyzer = CrossChainTraceAnalyzer()
        transfers = [
            _transfer("ethereum", SEED, COUNTERPARTY, tx_hash="0xa"),
            _transfer("ethereum", SEED, COUNTERPARTY, tx_hash="0xa"),  # duplicate
        ]
        with (
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
                AsyncMock(side_effect=[transfers, [], [], []]),
            ),
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_labels",
                AsyncMock(return_value={}),
            ),
        ):
            report = await analyzer.analyze(SEED, "ethereum")
        assert len(report.edges) == 1


class TestBridgeDetection:
    """Bridge hop detection in the fund-flow graph."""

    @pytest.mark.asyncio
    async def test_trace_bridge_hop_detected(self) -> None:
        """Transfers to a known bridge contract should create a bridge hop."""
        analyzer = CrossChainTraceAnalyzer()
        transfers = [_transfer("ethereum", SEED, ETH_BRIDGE)]
        with (
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
                AsyncMock(side_effect=[transfers, [], [], []]),
            ),
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_labels",
                AsyncMock(return_value={}),
            ),
        ):
            report = await analyzer.analyze(SEED, "ethereum")

        assert len(report.bridge_hops) == 1
        hop = report.bridge_hops[0]
        assert hop["bridge_contract"] == ETH_BRIDGE
        assert hop["source_chain"] == "ethereum"
        assert hop["destination_chain"] == "arbitrum"
        # Bridge presence adds risk
        assert report.risk_score >= 10

    @pytest.mark.asyncio
    async def test_trace_bridge_edge_marked(self) -> None:
        """Edges involving bridge contracts should carry is_bridge=True."""
        analyzer = CrossChainTraceAnalyzer()
        transfers = [_transfer("ethereum", SEED, ETH_BRIDGE)]
        with (
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
                AsyncMock(side_effect=[transfers, [], [], []]),
            ),
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_labels",
                AsyncMock(return_value={}),
            ),
        ):
            report = await analyzer.analyze(SEED, "ethereum")
        assert report.edges[0]["is_bridge"] is True
        assert report.edges[0]["bridge_destination_chain"] == "arbitrum"


class TestEntityFlagsAndRisk:
    """Labels and risk scoring for addresses in the trace."""

    @pytest.mark.asyncio
    async def test_malicious_label_flags_entity_and_risk(self) -> None:
        """Addresses labeled 'scam' should appear in entity_flags and raise risk."""
        analyzer = CrossChainTraceAnalyzer()
        transfers = [_transfer("ethereum", SEED, COUNTERPARTY)]
        labels = {
            COUNTERPARTY: {
                "label_name": "Known Scam Wallet",
                "label_category": "scam",
                "is_sanctioned": True,
            }
        }
        with (
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
                AsyncMock(side_effect=[transfers, [], [], []]),
            ),
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_labels",
                AsyncMock(return_value=labels),
            ),
        ):
            report = await analyzer.analyze(SEED, "ethereum")

        assert len(report.entity_flags) == 1
        assert "labeled as scam" in report.entity_flags[0]
        assert report.risk_score >= 15
        assert report.known_entities[COUNTERPARTY] == "Known Scam Wallet"
        node = next(n for n in report.nodes if n["address"] == COUNTERPARTY)
        assert node["role"] == "scam"
        assert node["is_sanctioned"] is True

    @pytest.mark.asyncio
    async def test_large_volume_adds_risk(self) -> None:
        """Receiving >100k ETH should increase the risk score."""
        analyzer = CrossChainTraceAnalyzer()
        big_value = str(200_000 * 10**18)
        transfers = [_transfer("ethereum", COUNTERPARTY, SEED, value=big_value)]
        with (
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
                AsyncMock(side_effect=[transfers, [], [], []]),
            ),
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_labels",
                AsyncMock(return_value={}),
            ),
        ):
            report = await analyzer.analyze(SEED, "ethereum")
        assert report.total_volume_received == pytest.approx(200_000.0)
        assert report.risk_score >= 15


class TestEdgeCases:
    """Empty traces and error handling."""

    @pytest.mark.asyncio
    async def test_trace_empty_transfers(self) -> None:
        """No transfers should yield an empty, low-risk report."""
        analyzer = CrossChainTraceAnalyzer()
        with (
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
                AsyncMock(side_effect=[[], [], [], []]),
            ),
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_labels",
                AsyncMock(return_value={}),
            ),
        ):
            report = await analyzer.analyze(SEED, "ethereum")
        assert report.nodes == []
        assert report.edges == []
        assert report.risk_score == 0.0
        assert report.risk_level == "low"
        assert report.error is None

    @pytest.mark.asyncio
    async def test_analyze_captures_query_error(self) -> None:
        """Query failures should be captured in the report error field."""
        analyzer = CrossChainTraceAnalyzer()
        with patch(
            "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
            AsyncMock(side_effect=ValueError("ClickHouse unavailable")),
        ):
            report = await analyzer.analyze(SEED, "ethereum")
        assert report.error is not None
        assert "ClickHouse" in report.error

    @pytest.mark.asyncio
    async def test_analyze_normalizes_address_case(self) -> None:
        """Seed addresses should be lowercased in the report."""
        analyzer = CrossChainTraceAnalyzer()
        with (
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_transfers",
                AsyncMock(side_effect=[[], [], [], []]),
            ),
            patch(
                "app.domains.scanners.cross_chain_trace.analyzer._fetch_labels",
                AsyncMock(return_value={}),
            ),
        ):
            report = await analyzer.analyze(SEED.upper(), "ethereum")
        assert report.seed_address == SEED

    def test_report_to_dict_shape(self) -> None:
        """_report_to_dict should expose the serializable report fields."""
        from app.domains.scanners.cross_chain_trace.models import CrossChainTraceReport

        report = CrossChainTraceReport(seed_address=SEED, seed_chain="ethereum")
        data = _report_to_dict(report)
        assert data["seed_address"] == SEED
        assert data["nodes"] == []
        assert data["edges"] == []
        assert data["risk_score"] == 0.0


class TestUtils:
    """Pure utility functions used by the analyzer."""

    def test_assign_role_priorities(self) -> None:
        """Role assignment should respect seed and label priority."""
        assert _assign_role(SEED, True, None) == "origin"
        assert _assign_role("0x1", False, "exchange") == "exchange"
        assert _assign_role("0x2", False, "mixer") == "mixer"
        assert _assign_role("0x3", False, "scam") == "scam"
        assert _assign_role("0x4", False, "bridge") == "bridge"
        assert _assign_role("0x5", False, "defi") == "defi"
        assert _assign_role("0x6", False, "sanctioned") == "sanctioned"
        assert _assign_role("0x7", False, None) == "intermediate"

    def test_evm_address_portable(self) -> None:
        """EVM addresses should be portable across EVM chains (minus source)."""
        chains = _evm_address_portable("0xabc", "ethereum")
        assert "ethereum" not in chains
        assert "bsc" in chains
        assert "polygon" in chains
        # Solana addresses are not portable to EVM chains
        assert _evm_address_portable("soladdr", "solana") == []


if __name__ == "__main__":
    pytest.main([__file__])
