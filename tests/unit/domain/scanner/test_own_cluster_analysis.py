"""Unit tests for ``own_cluster_analysis`` — funding-source clusters + coordinated buys.

Covers the pure clustering / verdict logic (including the deeper 2-hop
funding chain, time-node and supply-grab dimensions) and the fail-open
``unavailable`` payload (ClickHouse absent). No real ClickHouse — the query
layer is patched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.domains.scanner.engines import own_cluster_analysis as ca


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    ca.clear_cache()
    yield
    ca.clear_cache()


# ── Sample launch-buyer rows (shape of ``_query_launch_cluster`` output) ──


def _buyer(buyer: str, block_number: int, amount: float = 100.0) -> dict[str, Any]:
    return {
        "buyer": buyer,
        "block_number": block_number,
        "buy_count": 1,
        "amount": amount,
        "is_first_tx": False,
        "deployer_funded": False,
    }


LAUNCH_BUYERS = [
    _buyer("0xwallet_a", 100),
    _buyer("0xwallet_b", 100),
    _buyer("0xwallet_c", 100),
    _buyer("0xwallet_d", 100),
    _buyer("0xwallet_e", 101),
    _buyer("0xwallet_f", 102),
    _buyer("0xwallet_g", 102),
]

# One operator (0xfunder1) funded 5 of the first buyers; 2 bought organically.
FUNDER_MAP: dict[str, str] = {
    "0xwallet_a": "0xfunder1",
    "0xwallet_b": "0xfunder1",
    "0xwallet_c": "0xfunder1",
    "0xwallet_d": "0xfunder1",
    "0xwallet_e": "0xfunder1",
    "0xwallet_f": "0xfunder2",
    "0xwallet_g": "0xfunder3",
}

# Two wallets in the same block funded by the same operator.
COORDINATED_LAUNCH = [
    _buyer("0xwallet_a", 100),
    _buyer("0xwallet_b", 100),
    _buyer("0xwallet_c", 101),
]
COORDINATED_FUNDERS: dict[str, str] = {
    "0xwallet_a": "0xfunder1",
    "0xwallet_b": "0xfunder1",
    "0xwallet_c": "0xfunder2",
}

# Wallets share a funder but never buy in the same block — organic.
SPREAD_LAUNCH = [
    _buyer("0xwallet_a", 100),
    _buyer("0xwallet_b", 101),
    _buyer("0xwallet_c", 102),
]
SPREAD_FUNDERS: dict[str, str] = {
    "0xwallet_a": "0xfunder1",
    "0xwallet_b": "0xfunder1",
    "0xwallet_c": "0xfunder2",
}

# Funding chain: 0xfunder1 is itself a launch buyer of the token.
CHAIN_LAUNCH = [
    _buyer("0xfunder1", 99),
    _buyer("0xwallet_a", 100),
    _buyer("0xwallet_b", 100),
]
CHAIN_FUNDERS: dict[str, str] = {
    "0xfunder1": "0xouter",
    "0xwallet_a": "0xfunder1",
    "0xwallet_b": "0xfunder1",
}


# ── Funding-source clustering ──


class TestBuildFundingClusters:
    def test_groups_buyers_by_first_funder(self) -> None:
        clusters = ca._build_funding_clusters(LAUNCH_BUYERS, FUNDER_MAP)
        assert len(clusters) == 3  # 0xfunder1 / 0xfunder2 / 0xfunder3
        top = clusters[0]
        assert top["funder"] == "0xfunder1"
        assert top["wallet_count"] == 5
        assert "0xwallet_a" in top["wallets"]
        assert "0xwallet_e" in top["wallets"]

    def test_sorted_descending_by_wallet_count(self) -> None:
        clusters = ca._build_funding_clusters(LAUNCH_BUYERS, FUNDER_MAP)
        counts = [c["wallet_count"] for c in clusters]
        assert counts == sorted(counts, reverse=True)

    def test_buyers_without_funder_are_omitted(self) -> None:
        clusters = ca._build_funding_clusters([_buyer("0xorphan", 100)], {})
        assert clusters == []

    def test_total_funded_wallets_is_sum_of_cluster_sizes(self) -> None:
        clusters = ca._build_funding_clusters(LAUNCH_BUYERS, FUNDER_MAP)
        assert sum(c["wallet_count"] for c in clusters) == 7


# ── Coordinated-buy detection ──


class TestDetectCoordinatedBuys:
    def test_same_block_shared_funder_is_coordinated(self) -> None:
        coordinated, wallets, _chain_wallets = ca._detect_coordinated_buys(COORDINATED_LAUNCH, COORDINATED_FUNDERS)
        assert coordinated is True
        assert wallets == 2  # wallet_a + wallet_b bought block 100, same funder

    def test_shared_funder_across_blocks_is_not_coordinated(self) -> None:
        # wallet_a (block 100) and wallet_b (block 101) share a funder but
        # never buy in the same block — that is an organic-looking cluster.
        coordinated, wallets, _ = ca._detect_coordinated_buys(SPREAD_LAUNCH, SPREAD_FUNDERS)
        assert coordinated is False
        assert wallets == 0

    def test_funding_chain_flags_wallets_funded_by_launch_buyer(self) -> None:
        coordinated, wallets, chain_wallets = ca._detect_coordinated_buys(CHAIN_LAUNCH, CHAIN_FUNDERS)
        assert chain_wallets == 2  # wallet_a + wallet_b funded by 0xfunder1 (a buyer)
        # Same block + same funder pair also triggers coordinated here.
        assert coordinated is True
        assert wallets == 2

    def test_returns_zero_when_no_funders(self) -> None:
        coordinated, wallets, chain_wallets = ca._detect_coordinated_buys(LAUNCH_BUYERS, {})
        assert coordinated is False
        assert wallets == 0
        assert chain_wallets == 0


# ── Cluster line ──


class TestBuildClusterLine:
    def test_compact_summary_format(self) -> None:
        line = ca._build_cluster_line(3, 14, 9, "coordinated")
        assert line == "\U0001f578\ufe0f 3 funders \u2192 14 wallets \u00b7 largest cluster 9 \u00b7 coordinated"

    def test_organic_verdict(self) -> None:
        line = ca._build_cluster_line(4, 6, 2, "organic")
        assert line.endswith("\u00b7 organic")


# ── Time nodes (BubbleMaps-style funding-hour grouping) ──


class TestGroupTimeNodes:
    def test_buyers_funded_same_hour_form_one_time_node(self) -> None:
        ts_map = {
            "0xwallet_a": datetime(2026, 8, 16, 12, 5, 0),
            "0xwallet_b": datetime(2026, 8, 16, 12, 41, 0),
            "0xwallet_c": datetime(2026, 8, 16, 13, 2, 0),
        }
        buyers = [_buyer("0xwallet_a", 100), _buyer("0xwallet_b", 100), _buyer("0xwallet_c", 101)]
        assert ca._group_time_nodes(buyers, ts_map) == 1  # 12:00 bucket has 2

    def test_two_distinct_time_nodes(self) -> None:
        ts_map = {
            "0xwallet_a": datetime(2026, 8, 16, 12, 5, 0),
            "0xwallet_b": datetime(2026, 8, 16, 12, 41, 0),
            "0xwallet_c": datetime(2026, 8, 16, 13, 2, 0),
            "0xwallet_d": datetime(2026, 8, 16, 13, 30, 0),
        }
        buyers = [_buyer(a, 100) for a in ("0xwallet_a", "0xwallet_b", "0xwallet_c", "0xwallet_d")]
        assert ca._group_time_nodes(buyers, ts_map) == 2

    def test_single_buyers_per_hour_count_zero(self) -> None:
        ts_map = {
            "0xwallet_a": datetime(2026, 8, 16, 12, 5, 0),
            "0xwallet_b": datetime(2026, 8, 16, 13, 5, 0),
        }
        buyers = [_buyer("0xwallet_a", 100), _buyer("0xwallet_b", 101)]
        assert ca._group_time_nodes(buyers, ts_map) == 0

    def test_missing_timestamps_are_skipped(self) -> None:
        buyers = [_buyer("0xwallet_a", 100), _buyer("0xwallet_b", 100)]
        assert ca._group_time_nodes(buyers, {}) == 0
        assert ca._group_time_nodes(buyers, {"0xwallet_a": None, "0xwallet_b": "not-a-date"}) == 0

    def test_iso_string_timestamps_parse(self) -> None:
        ts_map = {
            "0xwallet_a": "2026-08-16T12:05:00",
            "0xwallet_b": "2026-08-16T12:41:00",
        }
        buyers = [_buyer("0xwallet_a", 100), _buyer("0xwallet_b", 100)]
        assert ca._group_time_nodes(buyers, ts_map) == 1


# ── Funding-chain depth (2-hop funder's-funder) ──


class TestDetectFundingChainDepth:
    def test_no_chain_depth_zero(self) -> None:
        buyers = [_buyer("0xwallet_a", 100), _buyer("0xwallet_b", 100)]
        funders = {"0xwallet_a": "0xfunder1", "0xwallet_b": "0xfunder2"}
        depth, two_hop = ca._detect_funding_chain_depth(buyers, funders, {})
        assert depth == 0
        assert two_hop == 0

    def test_one_hop_when_funder_is_launch_buyer(self) -> None:
        buyers = [_buyer("0xfunder1", 99), _buyer("0xwallet_a", 100), _buyer("0xwallet_b", 100)]
        funders = {"0xfunder1": "0xouter", "0xwallet_a": "0xfunder1", "0xwallet_b": "0xfunder1"}
        depth, two_hop = ca._detect_funding_chain_depth(buyers, funders, {"0xfunder1": "0xouter"})
        assert depth == 1
        assert two_hop == 0

    def test_two_hop_when_grandfunder_is_launch_buyer(self) -> None:
        # 0xgrandfunder bought the token, funded 0xfunder1, which funded buyers.
        buyers = [_buyer("0xgrandfunder", 98), _buyer("0xwallet_a", 100), _buyer("0xwallet_b", 100)]
        funders = {"0xgrandfunder": "0xdeployer", "0xwallet_a": "0xfunder1", "0xwallet_b": "0xfunder1"}
        grandfunders = {"0xfunder1": "0xgrandfunder", "0xgrandfunder": "0xdeployer"}
        depth, two_hop = ca._detect_funding_chain_depth(buyers, funders, grandfunders)
        assert depth == 2
        assert two_hop == 2  # both wallets traced through to the grandfunder

    def test_two_hop_shared_grandfunder_across_funders(self) -> None:
        # One operator (0xgf) armed two distinct funders → layered pattern.
        buyers = [_buyer("0xwallet_a", 100), _buyer("0xwallet_b", 100), _buyer("0xwallet_c", 101)]
        funders = {"0xwallet_a": "0xfunder1", "0xwallet_b": "0xfunder1", "0xwallet_c": "0xfunder2"}
        grandfunders = {"0xfunder1": "0xgf", "0xfunder2": "0xgf"}
        depth, two_hop = ca._detect_funding_chain_depth(buyers, funders, grandfunders)
        assert depth == 2
        assert two_hop == 3


# ── Supply grab (bundled and holding vs bundled and dumped) ──


class TestComputeSupplyGrab:
    def test_holding_when_cluster_keeps_large_share(self) -> None:
        buyers = [
            _buyer("0xwallet_a", 100, amount=100.0),
            _buyer("0xwallet_b", 100, amount=100.0),
            _buyer("0xwallet_c", 101, amount=100.0),
        ]
        balances = {"0xwallet_a": 400.0, "0xwallet_b": 400.0, "0xwallet_c": 200.0}
        total = 1000.0
        sg = ca._compute_supply_grab(buyers, balances, total)
        assert sg["status"] == "holding"  # 100% of tracked supply still held
        assert sg["held_pct"] == 100.0
        assert sg["dumped_pct"] == 0.0
        assert sg["exited_wallets"] == 0

    def test_holding_threshold_requires_ten_percent(self) -> None:
        buyers = [_buyer("0xwallet_a", 100, amount=100.0), _buyer("0xwallet_b", 100, amount=100.0)]
        balances = {"0xwallet_a": 8.0, "0xwallet_b": 5.0}
        total = 1000.0
        sg = ca._compute_supply_grab(buyers, balances, total)
        assert sg["status"] == "none"  # 1.3% held < 10% threshold

    def test_dumped_when_cluster_exited_most_of_buy(self) -> None:
        buyers = [
            _buyer("0xwallet_a", 100, amount=100.0),
            _buyer("0xwallet_b", 100, amount=100.0),
        ]
        balances = {"0xwallet_a": 0.0, "0xwallet_b": 0.0}
        total = 1000.0
        sg = ca._compute_supply_grab(buyers, balances, total)
        assert sg["status"] == "dumped"
        assert sg["held_pct"] == 0.0
        assert sg["dumped_pct"] == 100.0
        assert sg["exited_wallets"] == 2

    def test_partial_exit_is_dumped_when_over_half(self) -> None:
        buyers = [
            _buyer("0xwallet_a", 100, amount=100.0),
            _buyer("0xwallet_b", 100, amount=100.0),
        ]
        balances = {"0xwallet_a": 0.0, "0xwallet_b": 30.0}
        total = 1000.0
        sg = ca._compute_supply_grab(buyers, balances, total)
        assert sg["status"] == "dumped"  # 50% of bought supply exited
        assert sg["dumped_pct"] == 50.0

    def test_no_balance_data_is_none(self) -> None:
        buyers = [_buyer("0xwallet_a", 100, amount=100.0)]
        sg = ca._compute_supply_grab(buyers, {}, 0.0)
        assert sg["status"] == "none"
        assert sg["held_pct"] == 0.0
        assert sg["dumped_pct"] == 0.0


# ── Richer bundle verdict line ──


class TestBuildBundleVerdictLine:
    def test_full_verdict_line_format(self) -> None:
        supply_grab = {"status": "holding", "held_pct": 62.0, "dumped_pct": 12.0, "exited_wallets": 1}
        line = ca._build_bundle_verdict_line(14, 9, 2, supply_grab, 3, True)
        assert line == (
            "\U0001f534 Bundle: 14 wallets \u00b7 same-slot 9 \u00b7 2-hop funder chain "
            "\u00b7 holding 62.0% (dumped 12.0%) \u00b7 3 time-nodes"
        )

    def test_omits_dumped_when_zero(self) -> None:
        supply_grab = {"status": "holding", "held_pct": 80.0, "dumped_pct": 0.0, "exited_wallets": 0}
        line = ca._build_bundle_verdict_line(6, 4, 1, supply_grab, 2, True)
        assert "dumped" not in line
        assert "holding 80.0%" in line
        assert "1-hop funder chain" in line

    def test_fail_open_dash_without_bundle_evidence(self) -> None:
        supply_grab = {"status": "none", "held_pct": 0.0, "dumped_pct": 0.0, "exited_wallets": 0}
        assert ca._build_bundle_verdict_line(2, 0, 0, supply_grab, 0, False) == "\u2014"
        # Coordinated alone is enough evidence to render.
        line = ca._build_bundle_verdict_line(2, 0, 1, supply_grab, 0, True)
        assert line.startswith("\U0001f534 Bundle: 2 wallets")

    def test_dumped_only_verdict(self) -> None:
        supply_grab = {"status": "dumped", "held_pct": 0.0, "dumped_pct": 100.0, "exited_wallets": 2}
        line = ca._build_bundle_verdict_line(2, 2, 0, supply_grab, 1, False)
        assert "holding 0.0% (dumped 100.0%)" in line
        assert "1-hop funder chain" in line


# ── Public API: fail-open without ClickHouse ──


class TestGetOwnClusterAnalysis:
    async def test_unavailable_payload_when_query_layer_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            return None

        monkeypatch.setattr(ca, "run_sync", _fake_run_sync)
        result = await ca.get_own_cluster_analysis("0xtoken", "ethereum")
        assert result["result"] == "unavailable"
        assert result["cluster_line"] == "\u2014"
        assert result["verdict"] == "unknown"
        assert result["coordinated"] is False

    async def test_never_raises_on_query_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("clickhouse down")

        monkeypatch.setattr(ca, "run_sync", _boom)
        result = await ca.get_own_cluster_analysis("0xtoken", "ethereum")
        assert result["result"] == "unavailable"
        assert result["cluster_line"] == "\u2014"

    async def test_scanned_payload_with_fake_query_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if fn is ca._query_launch_cluster:
                return {
                    "deploy_block": 99,
                    "launch_buyers": COORDINATED_LAUNCH,
                    "cluster_blocks": [{"block_number": 100, "buyer_count": 2}],
                }
            if fn is ca._query_first_funders:
                return [(addr, funder, "0xtx", None) for addr, funder in COORDINATED_FUNDERS.items()]
            return None

        monkeypatch.setattr(ca, "run_sync", _fake_run_sync)
        result = await ca.get_own_cluster_analysis("0xtoken", "ethereum")
        assert result["result"] == "scanned"
        assert result["verdict"] == "coordinated"
        assert result["coordinated_buyers"] == 2
        assert result["distinct_funders"] == 2
        assert result["largest_cluster_size"] == 2
        assert "\U0001f578\ufe0f" in result["cluster_line"]

    async def test_cache_hit_returns_clean_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            return None

        monkeypatch.setattr(ca, "run_sync", _fake_run_sync)
        await ca.get_own_cluster_analysis("0xtoken", "ethereum")
        # Second call hits the cache — no query layer involved.
        result = await ca.get_own_cluster_analysis("0xtoken", "ethereum")
        assert result["result"] == "unavailable"
        assert "_cached_at" not in result  # internal key stripped on cache hit

    async def test_scanned_payload_with_deeper_dimensions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """2-hop chain + time nodes + supply grab all flow into the payload."""

        def _funding_rows(holders: list[str]) -> list[tuple[Any, ...]]:
            rows: list[tuple[Any, ...]] = []
            for h in holders:
                funder = COORDINATED_FUNDERS.get(h)
                if funder:
                    rows.append((h, funder, "0xtx", datetime(2026, 8, 16, 12, 30, 0)))
            return rows

        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if fn is ca._query_launch_cluster:
                return {
                    "deploy_block": 99,
                    "launch_buyers": COORDINATED_LAUNCH,
                    "cluster_blocks": [{"block_number": 100, "buyer_count": 2}],
                }
            if fn is ca._query_first_funders:
                # First call: launch-buyer funders. Second call: funders'
                # own funders (grandfunders) — same query, reused.
                holders = list(args[0])
                if holders and holders[0] in ("0xfunder1", "0xfunder2"):
                    return [("0xfunder1", "0xgf", "0xtx", None), ("0xfunder2", "0xgf", "0xtx", None)]
                return _funding_rows(holders)
            if fn is ca._query_tracked_balances:
                return [
                    ("0xwallet_a", 400.0),
                    ("0xwallet_b", 400.0),
                    ("0xwallet_c", 200.0),
                    ("0xother", 100.0),
                ]
            return None

        monkeypatch.setattr(ca, "run_sync", _fake_run_sync)
        result = await ca.get_own_cluster_analysis("0xtoken", "ethereum")
        assert result["result"] == "scanned"
        assert result["verdict"] == "coordinated"
        # 2-hop: funders 0xfunder1/0xfunder2 share grandfunder 0xgf.
        assert result["funding_chain_depth"] == 2
        assert result["two_hop_chain_buyers"] == 3
        # Time nodes: all three buyers funded in the same hour → 1 node.
        assert result["time_nodes"] == 1
        # Supply grab: the 3 launch buyers hold 1000/1100 of tracked supply.
        assert result["supply_grab"]["status"] == "holding"
        assert result["supply_grab"]["held_pct"] == 90.9
        # Same-slot = busiest launch block (2 buyers).
        assert result["same_slot_buyers"] == 2
        # Richer verdict line.
        assert result["bundle_line"].startswith("\U0001f534 Bundle: 3 wallets")

    async def test_deeper_dimensions_fail_open_when_queries_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Missing grandfunder/balance rows degrade — never raise, never fabricate."""

        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if fn is ca._query_launch_cluster:
                return {
                    "deploy_block": 99,
                    "launch_buyers": SPREAD_LAUNCH,
                    "cluster_blocks": [{"block_number": 100, "buyer_count": 1}],
                }
            if fn is ca._query_first_funders:
                return [(h, f, "0xtx", None) for h, f in SPREAD_FUNDERS.items()]
            return None  # no balances, no grandfunders

        monkeypatch.setattr(ca, "run_sync", _fake_run_sync)
        result = await ca.get_own_cluster_analysis("0xtoken", "ethereum")
        assert result["result"] == "scanned"
        assert result["funding_chain_depth"] == 0
        assert result["two_hop_chain_buyers"] == 0
        assert result["time_nodes"] == 0
        assert result["supply_grab"]["status"] == "none"
        # Organic spread launch → no bundle evidence → fail-open "—".
        assert result["bundle_line"] == "\u2014"

    async def test_query_layer_error_never_breaks_bundle_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A failing balance query must not abort the whole cluster analysis."""

        async def _fake_run_sync(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if fn is ca._query_launch_cluster:
                return {
                    "deploy_block": 99,
                    "launch_buyers": COORDINATED_LAUNCH,
                    "cluster_blocks": [{"block_number": 100, "buyer_count": 2}],
                }
            if fn is ca._query_first_funders:
                return [(addr, funder, "0xtx", None) for addr, funder in COORDINATED_FUNDERS.items()]
            if fn is ca._query_tracked_balances:
                raise RuntimeError("clickhouse timeout")
            return None

        monkeypatch.setattr(ca, "run_sync", _fake_run_sync)
        result = await ca.get_own_cluster_analysis("0xtoken", "ethereum")
        assert result["result"] == "scanned"
        assert result["supply_grab"]["status"] == "none"  # fail-open
        assert result["funding_chain_depth"] == 0
