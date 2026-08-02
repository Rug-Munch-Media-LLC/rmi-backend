"""Unit tests for wallet_flow — C1 wallet-to-wallet flow graph engine.

Covers ``build_flow_graph()`` against a fake ClickHouse client
(evm_transfers / wallet_labels / dex_events):

  - Hop-1 expansion (transfers FROM/TO the root wallet)
  - Hop-2 expansion (top counterparties)
  - Entity labeling (CEX / DEX / bridge / mixer / contract / EOA)
  - USD normalization via the price oracle mock
  - max_nodes cap enforcement
  - Empty-wallet graceful degradation
  - Redis fast-cache path

All external dependencies (ClickHouse, Redis, price oracle) are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.wallet_flow import (
    _ZERO_ADDR,
    _classify_node,
    _rank_counterparties,
    build_flow_graph,
    clear_cache,
)

_WALLET = "0x1111111111111111111111111111111111111111"
_CP1 = "0x2222222222222222222222222222222222222222"
_CP2 = "0x3333333333333333333333333333333333333333"
_CP3 = "0x4444444444444444444444444444444444444444"
_CP4 = "0x5555555555555555555555555555555555555555"
_CP5 = "0x6666666666666666666666666666666666666666"
_CP6 = "0x7777777777777777777777777777777777777777"
_TOKEN = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


class FakeCHClient:
    """Sync ClickHouse stand-in dispatching on wallet_flow SQL fragments."""

    def __init__(
        self,
        hop1: list[tuple] | None = None,
        hop2: list[tuple] | None = None,
        labels: list[tuple] | None = None,
        contracts: list[tuple] | None = None,
    ) -> None:
        self.hop1 = hop1 or []
        self.hop2 = hop2 or []
        self.labels = labels or []
        self.contracts = contracts or []
        self.executed: list[str] = []

    def execute(self, sql: str, params: dict | None = None, settings: dict | None = None) -> object:
        self.executed.append(sql)
        if "wallet_memory.wallet_labels" in sql and "address IN" in sql:
            return self.labels
        if "wallet_memory.dex_events" in sql and "contract_address IN" in sql:
            return self.contracts
        if "from_address = %(wallet)s OR to_address = %(wallet)s" in sql:
            return self.hop1
        if "from_address = %(cp)s OR to_address = %(cp)s" in sql:
            return self.hop2
        return []


def _mock_ch(fake: FakeCHClient):
    return patch("app.domains.scanner.engines.wallet_flow._get_ch_client", return_value=fake)


def _mock_usd(value: float):
    return patch(
        "app.domains.scanner.engines.wallet_flow._normalize_token_usd",
        AsyncMock(return_value=value),
    )


def _no_redis_get():
    return patch("app.domains.scanner.engines.wallet_flow._redis_get", AsyncMock(return_value=None))


def _no_redis_set():
    return patch("app.domains.scanner.engines.wallet_flow._redis_set", AsyncMock(return_value=True))


class TestBuildFlowGraph:
    """Integration tests for build_flow_graph against a fake ClickHouse."""

    def setup_method(self) -> None:
        clear_cache()

    @pytest.mark.asyncio
    async def test_hop1_expansion_both_directions(self) -> None:
        """Transfers FROM and TO the root wallet both produce graph edges."""
        fake = FakeCHClient(
            hop1=[
                (_WALLET, _CP1, _TOKEN, 5000, "0xaaa"),  # outflow edge
                (_CP2, _WALLET, _TOKEN, 3000, "0xbbb"),  # inflow edge
            ]
        )
        with (
            _mock_ch(fake),
            _mock_usd(8000.0),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await build_flow_graph(_WALLET, "ethereum", hops=1)

        assert out["result"] == "scanned"
        node_ids = {n["id"] for n in out["nodes"]}
        assert _CP1 in node_ids  # outflow counterparty
        assert _CP2 in node_ids  # inflow counterparty
        edge_from = {e["to"] for e in out["edges"] if e["from"] == _WALLET}
        assert _CP1 in edge_from
        edge_to = {e["from"] for e in out["edges"] if e["to"] == _WALLET}
        assert _CP2 in edge_to

    @pytest.mark.asyncio
    async def test_hop2_expansion_adds_counterparty_neighbors(self) -> None:
        """Top counterparties are expanded one extra hop with new nodes."""
        third = _CP3
        fake = FakeCHClient(
            hop1=[(_WALLET, _CP1, _TOKEN, 5000, "0xaaa")],
            hop2=[(_CP1, third, _TOKEN, 3000, "0xbbb")],
        )
        with (
            _mock_ch(fake),
            _mock_usd(8000.0),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await build_flow_graph(_WALLET, "ethereum", hops=2)

        assert out["result"] == "scanned"
        node_ids = {n["id"] for n in out["nodes"]}
        assert third in node_ids  # hop-2 node added
        assert _WALLET in node_ids
        # Hop-2 edge (CP1 -> third) present with evidence
        hop2_edges = [e for e in out["edges"] if e["from"] == _CP1 and e["to"] == third]
        assert hop2_edges
        assert hop2_edges[0]["tx_hashes"] == ["0xbbb"]

    @pytest.mark.asyncio
    async def test_usd_normalization_proportional_allocation(self) -> None:
        """One oracle call per token; per-edge USD is allocated proportionally."""
        fake = FakeCHClient(
            hop1=[
                (_WALLET, _CP1, _TOKEN, 4000, "0xaaa"),
                (_WALLET, _CP2, _TOKEN, 1000, "0xbbb"),
            ]
        )
        with (
            _mock_ch(fake),
            _mock_usd(5000.0) as usd_mock,
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await build_flow_graph(_WALLET, "ethereum", hops=1)

        # token_raw_total = 5000; oracle returns 5000 USD for the whole token
        usd_mock.assert_awaited_once()
        edge_usd = {e["to"]: e["usd"] for e in out["edges"]}
        assert edge_usd[_CP1] == pytest.approx(4000.0)
        assert edge_usd[_CP2] == pytest.approx(1000.0)

    @pytest.mark.asyncio
    async def test_entity_labeling_all_node_types(self) -> None:
        """Labels classify nodes as CEX/DEX/bridge/mixer/contract/EOA."""
        fake = FakeCHClient(
            hop1=[
                (_WALLET, _CP1, _TOKEN, 100, "0xaaa"),  # CEX
                (_WALLET, _CP2, _TOKEN, 100, "0xbbb"),  # DEX
                (_WALLET, _CP3, _TOKEN, 100, "0xccc"),  # bridge
                (_WALLET, _CP4, _TOKEN, 100, "0xddd"),  # mixer
                (_WALLET, _CP5, _TOKEN, 100, "0xeee"),  # contract
                (_WALLET, _CP6, _TOKEN, 100, "0xfff"),  # EOA (no label)
            ],
            labels=[
                (_CP1, "Binance Hot Wallet", "exchange", "cex", "eth_labels", 0),
                (_CP2, "Uniswap V3 Router", "dex", "router", "eth_labels", 0),
                (_CP3, "Wormhole Bridge", "bridge", "", "eth_labels", 0),
                (_CP4, "Tornado Cash", "mixer", "", "eth_labels", 0),
            ],
            contracts=[(_CP5,)],
        )
        with (
            _mock_ch(fake),
            _mock_usd(600.0),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await build_flow_graph(_WALLET, "ethereum", hops=1)

        node_type = {n["id"]: n["type"] for n in out["nodes"]}
        assert node_type[_CP1] == "CEX"
        assert node_type[_CP2] == "DEX"
        assert node_type[_CP3] == "bridge"
        assert node_type[_CP4] == "mixer"
        assert node_type[_CP5] == "contract"
        assert node_type[_CP6] == "EOA"
        # Root wallet has no label / contract row -> EOA
        assert node_type[_WALLET] == "EOA"

    @pytest.mark.asyncio
    async def test_max_nodes_cap_enforced(self) -> None:
        """The node list is truncated to max_nodes even with many counterparties."""
        many_cps = [(f"0x{i:040d}",) for i in range(10)]
        hop1 = [(_WALLET, addr, _TOKEN, 1000 + i, f"0xhash{i}") for i, (addr,) in enumerate(many_cps)]
        fake = FakeCHClient(hop1=hop1)
        with (
            _mock_ch(fake),
            _mock_usd(20000.0),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await build_flow_graph(_WALLET, "ethereum", hops=1, max_nodes=5)

        assert out["result"] == "scanned"
        assert out["node_count"] <= 5
        node_ids = [n["id"] for n in out["nodes"]]
        assert _WALLET in node_ids  # root wallet always included

    @pytest.mark.asyncio
    async def test_empty_wallet_returns_unavailable(self) -> None:
        """A wallet with zero transfers degrades gracefully (unavailable)."""
        fake = FakeCHClient(hop1=[])
        with (
            _mock_ch(fake),
            _no_redis_get(),
            _no_redis_set(),
        ):
            out = await build_flow_graph(_WALLET, "ethereum")

        assert out["result"] == "unavailable"
        assert out["node_count"] == 0
        assert out["edge_count"] == 0
        assert "no transfer history" in out.get("note", "")

    @pytest.mark.asyncio
    async def test_redis_cache_hit_skips_clickhouse(self) -> None:
        """A cached Redis result is returned without querying ClickHouse."""
        cached = {
            "check": "wallet_flow",
            "result": "scanned",
            "wallet": _WALLET,
            "chain": "ethereum",
            "hops": 2,
            "nodes": [{"id": _WALLET, "type": "EOA", "net_usd": 0, "tx_count": 0}],
            "edges": [],
            "node_count": 1,
            "edge_count": 0,
        }
        fake = FakeCHClient(hop1=[])
        with (
            _mock_ch(fake),
            patch("app.domains.scanner.engines.wallet_flow._redis_get", AsyncMock(return_value=cached)),
            patch("app.domains.scanner.engines.wallet_flow._compute_graph", AsyncMock()) as compute,
        ):
            out = await build_flow_graph(_WALLET, "ethereum")

        assert out == cached
        compute.assert_not_awaited()
        assert fake.executed == []  # no ClickHouse query executed


class TestClassifyNode:
    """Unit tests for the pure node-classification helper."""

    @pytest.mark.parametrize(
        ("address", "labels", "contracts", "expected_type"),
        [
            ("0xdead", [{"label_category": "exchange", "label_name": "Binance"}], set(), "CEX"),
            ("0xdead", [{"label_category": "cex", "label_name": "OKX"}], set(), "CEX"),
            ("0xdead", [{"label_category": "other", "label_name": "Kraken Deposit"}], set(), "CEX"),
            ("0xdead", [{"label_category": "dex", "label_name": "Uniswap"}], set(), "DEX"),
            ("0xdead", [{"label_category": "router", "label_name": "1inch Router"}], set(), "DEX"),
            ("0xdead", [{"label_category": "bridge", "label_name": "Wormhole"}], set(), "bridge"),
            ("0xdead", [{"label_category": "mixer", "label_name": "Tornado Cash"}], set(), "mixer"),
            (_CP1, [], {_CP1}, "contract"),
            ("0xdead", [], set(), "EOA"),
        ],
    )
    def test_classify_node_types(self, address, labels, contracts, expected_type) -> None:
        """_classify_node maps labels/contract addresses to node types."""
        node_type, _label = _classify_node(address, labels, contracts)
        assert node_type == expected_type


class TestRankCounterparties:
    """Unit tests for the hop-2 counterparty ranking helper."""

    def test_ranks_by_raw_volume_and_excludes_zero_address(self) -> None:
        """Zero address (burn/mint) counterparties are never ranked."""
        edge_raw = {
            (_WALLET, _CP1, _TOKEN): {"raw": 100.0, "count": 1, "hashes": []},
            (_WALLET, _CP2, _TOKEN): {"raw": 500.0, "count": 1, "hashes": []},
            (_WALLET, _ZERO_ADDR, _TOKEN): {"raw": 9999.0, "count": 1, "hashes": []},
        }
        ranked = _rank_counterparties(_WALLET, edge_raw)
        assert ranked == [(_CP2, 500.0), (_CP1, 100.0)]
        assert all(addr != _ZERO_ADDR for addr, _ in ranked)


if __name__ == "__main__":
    pytest.main([__file__])
