"""Unit tests for own_decentralization — BubbleMaps-style cluster map + score.

Covers:
  - Pure scoring components (concentration / funding diversity / whale share)
  - Bubble-map line rendering (emoji-scaled bubbles + cluster sizes + time nodes)
  - Neo4j connected-component clustering (union-find, mocked driver)
  - Fail-open "—" when holder data is unavailable
  - End-to-end ``get_own_decentralization`` with pre-fetched engine payloads
  - Cache serving the second call
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.domains.scanner.engines import own_decentralization as dec

_TOKEN = "0xtoken"
_CHAIN = "ethereum"
_DEPLOYER = "0xdeployer"


# ── Sample engine payloads ────────────────────────────────────────────────


def _holder(address: str, pct: float) -> dict[str, Any]:
    return {"address": address, "role": "whale" if pct >= 5 else "fish", "percentage": pct, "funder": None}


def _wallet_map(holders: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "check": "wallet_map",
        "result": "scanned",
        "wallet_map_line": "🐳1 🐟4",
        "roles": {"dev": 0, "whale": 1, "fish": 4, "shrimp": 0, "fresh": 0, "bundle": 0},
        "classified": holders or [_holder("0xbig", 12.0), _holder("0xmed", 3.0)],
        "confidence": 0.8,
    }


def _cluster(
    distinct_funders: int = 3,
    largest: int = 5,
    coordinated: int = 2,
) -> dict[str, Any]:
    return {
        "check": "cluster_analysis",
        "result": "scanned",
        "cluster_line": "🕸️ 3 funders → 14 wallets · largest cluster 5 · coordinated",
        "distinct_funders": distinct_funders,
        "funded_wallets": 14,
        "largest_cluster_size": largest,
        "clusters": [
            {"funder": "0xf1", "wallet_count": largest, "wallets": [f"0xw{i}" for i in range(largest)]},
            {"funder": "0xf2", "wallet_count": 3, "wallets": ["0xa", "0xb", "0xc"]},
            {"funder": "0xf3", "wallet_count": 2, "wallets": ["0xd", "0xe"]},
        ],
        "coordinated": True,
        "coordinated_buyers": coordinated,
        "funding_chain_buyers": 0,
        "verdict": "coordinated",
    }


# ═══════════════════════════════════════════════════════════════════════════
# Pure scoring components
# ═══════════════════════════════════════════════════════════════════════════


class TestScoreComponents:
    def test_concentration_full_credit_for_healthy_distribution(self) -> None:
        # top-10 = 20%, top-20 = 40% → no penalty → 40/40.
        assert dec._score_concentration(20.0, 40.0) == pytest.approx(40.0)

    def test_concentration_penalizes_heavy_top_10(self) -> None:
        # top-10 = 60% → (60-20)*0.5 = 20 penalty; top-20 = 80% → (80-40)*0.25 = 10.
        assert dec._score_concentration(60.0, 80.0) == pytest.approx(10.0)

    def test_concentration_floor_at_zero(self) -> None:
        assert dec._score_concentration(100.0, 100.0) == pytest.approx(0.0)

    def test_funding_diversity_tiers(self) -> None:
        assert dec._score_funding_diversity(10, 100, 5) == pytest.approx(30.0)
        assert dec._score_funding_diversity(6, 100, 5) == pytest.approx(24.0)
        assert dec._score_funding_diversity(4, 100, 5) == pytest.approx(18.0)
        assert dec._score_funding_diversity(3, 100, 5) == pytest.approx(12.0)
        assert dec._score_funding_diversity(2, 100, 5) == pytest.approx(6.0)
        assert dec._score_funding_diversity(1, 100, 1) == pytest.approx(2.0)
        assert dec._score_funding_diversity(0, 100, 1) == pytest.approx(0.0)

    def test_funding_diversity_dominance_penalty(self) -> None:
        # One funder arms 9/10 wallets (>=60% dominance) → half credit.
        assert dec._score_funding_diversity(3, 10, 9) == pytest.approx(6.0)

    def test_whale_share_tiers(self) -> None:
        assert dec._score_whale_share(10.0) == pytest.approx(30.0)
        assert dec._score_whale_share(25.0) == pytest.approx(20.0)
        assert dec._score_whale_share(40.0) == pytest.approx(12.0)
        assert dec._score_whale_share(55.0) == pytest.approx(6.0)
        assert dec._score_whale_share(80.0) == pytest.approx(0.0)

    def test_label_boundaries(self) -> None:
        assert dec._decentralization_label(80) == "healthy"
        assert dec._decentralization_label(50) == "moderate"
        assert dec._decentralization_label(20) == "concentrated"


# ═══════════════════════════════════════════════════════════════════════════
# Bubble-map line rendering
# ═══════════════════════════════════════════════════════════════════════════


class TestBubbleLine:
    def test_bubbles_scaled_by_percentage(self) -> None:
        holders = [_holder("a", 20.0), _holder("b", 7.0), _holder("c", 3.0), _holder("d", 0.5)]
        line = dec._build_bubble_line(holders, [], 0)
        assert line == "\U0001f534\U0001f7e0\U0001f7e1\U0001f7e2"  # 🔴🟠🟡🟢

    def test_cluster_sizes_and_time_nodes_rendered(self) -> None:
        holders = [_holder("a", 12.0), _holder("b", 4.0)]
        clusters = [
            {"funder": "0xf1", "wallet_count": 5, "wallets": []},
            {"funder": "0xf2", "wallet_count": 3, "wallets": []},
        ]
        line = dec._build_bubble_line(holders, clusters, 2)
        assert "\U0001f578\ufe0f 5\u00b73" in line  # 🕸️ 5·3
        assert "\u23f1\ufe0f 2 same-time" in line  # ⏱️ 2 same-time
        assert line.startswith("\U0001f534\U0001f7e1")  # 🔴🟡

    def test_bubble_cap_appends_plus_n(self) -> None:
        holders = [_holder(f"0x{i}", float(i + 1)) for i in range(12)]
        line = dec._build_bubble_line(holders, [], 0)
        assert line.endswith("+2")

    def test_no_time_nodes_segment_when_zero(self) -> None:
        line = dec._build_bubble_line([_holder("a", 2.0)], [], 0)
        assert "\u23f1" not in line


# ═══════════════════════════════════════════════════════════════════════════
# build_decentralization (pure, from engine payloads)
# ═══════════════════════════════════════════════════════════════════════════


class TestBuildDecentralization:
    def test_score_and_lines_from_wallet_map_and_clusters(self) -> None:
        holders = [
            _holder("0xbig1", 30.0),
            _holder("0xbig2", 20.0),
            _holder("0xmid1", 5.0),
            _holder("0xmid2", 3.0),
            _holder("0xsmall", 1.0),
        ]
        out = dec.build_decentralization(_wallet_map(holders), _cluster(distinct_funders=3, largest=5, coordinated=2))

        assert out["check"] == "decentralization"
        assert out["result"] == "scanned"
        # top10/top20 = 59% → conc 15.8; 3 funders → funding 12; whales hold
        # 55% → whale 6 → total ≈ 34 (moderate at the >=34 boundary).
        assert out["decentralization_score"] == 34
        assert out["decentralization_line"].startswith("\U0001fae7 Decentralization: 34/100 ")
        assert "(moderate)" in out["decentralization_line"]

    def test_score_math_is_consistent_with_components(self) -> None:
        holders = [_holder("a", 8.0), _holder("b", 8.0), _holder("c", 2.0), _holder("d", 1.0)]
        out = dec.build_decentralization(_wallet_map(holders), _cluster(distinct_funders=4, largest=5, coordinated=1))
        comp = out["breakdown"]["components"]
        expected = round(comp["concentration"] + comp["funding_diversity"] + comp["whale_share"])
        assert out["decentralization_score"] == max(0, min(100, expected))
        assert out["breakdown"]["top10_pct"] == pytest.approx(19.0)
        assert out["breakdown"]["whale_share"] == pytest.approx(16.0)
        assert out["breakdown"]["time_nodes"] == 1
        assert out["breakdown"]["cluster_source"] == "clickhouse"

    def test_missing_cluster_payload_degrades_to_holder_only_score(self) -> None:
        out = dec.build_decentralization(_wallet_map(), None)
        assert out["result"] == "scanned"
        assert out["breakdown"]["distinct_funders"] == 0
        assert out["breakdown"]["components"]["funding_diversity"] == 0
        # Score still computable from concentration + whale share.
        assert out["decentralization_score"] is not None
        assert "🕸️" not in out["bubble_line"]

    def test_unavailable_cluster_payload_is_tolerated(self) -> None:
        unavailable = {"check": "cluster_analysis", "result": "unavailable", "cluster_line": "\u2014"}
        out = dec.build_decentralization(_wallet_map(), unavailable)
        assert out["result"] == "scanned"
        assert out["bubble_line"] != "\u2014"


# ═══════════════════════════════════════════════════════════════════════════
# Neo4j connected-component clustering (pure, mocked driver)
# ═══════════════════════════════════════════════════════════════════════════


class TestConnectedComponents:
    def test_two_wallets_linked_forms_cluster(self) -> None:
        rows = [("0xa", ["0xb"]), ("0xb", ["0xa"])]
        clusters = dec._connected_components(rows)
        assert len(clusters) == 1
        assert clusters[0]["wallet_count"] == 2
        assert clusters[0]["source"] == "neo4j"
        assert set(clusters[0]["wallets"]) == {"0xa", "0xb"}

    def test_isolated_wallets_do_not_cluster(self) -> None:
        rows = [("0xa", []), ("0xb", []), ("0xc", [])]
        assert dec._connected_components(rows) == []

    def test_three_hop_chain_collapses_into_one_cluster(self) -> None:
        rows = [("0xa", ["0xb"]), ("0xb", ["0xa", "0xc"]), ("0xc", ["0xb"])]
        clusters = dec._connected_components(rows)
        assert len(clusters) == 1
        assert clusters[0]["wallet_count"] == 3


class TestQueryNeo4jClusters:
    @pytest.mark.asyncio
    async def test_fail_open_when_driver_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dec, "_NEO4J_REL_TYPES", "SAME_AS")
        with patch("app.domains.wallet.neo4j_client.get_neo4j_driver", return_value=None):
            clusters, used = await dec._query_neo4j_clusters(["0xa", "0xb"])
        assert clusters == []
        assert used is False

    @pytest.mark.asyncio
    async def test_uses_neo4j_when_edges_exist(self) -> None:
        class FakeSession:
            def __enter__(self):
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
                assert "SAME_AS|FUNDED_BY_SAME|CLONE_OF|BEHAVIORAL_MATCH" in query
                assert "0xa" in params["holders"]
                return [
                    {"a": "0xa", "linked": ["0xb"]},
                    {"a": "0xb", "linked": ["0xa"]},
                ]

        class FakeDriver:
            def session(self) -> FakeSession:
                return FakeSession()

        with patch("app.domains.wallet.neo4j_client.get_neo4j_driver", return_value=FakeDriver()):
            clusters, used = await dec._query_neo4j_clusters(["0xA", "0xb"])
        assert used is True
        assert len(clusters) == 1
        assert clusters[0]["wallet_count"] == 2


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════


class TestGetOwnDecentralization:
    def setup_method(self) -> None:
        dec.clear_cache()

    @pytest.mark.asyncio
    async def test_fail_open_when_wallet_map_unavailable(self) -> None:
        unavailable_wm = {"check": "wallet_map", "result": "unavailable", "wallet_map_line": "\u2014"}
        with patch("app.domains.scanner.engines.own_decentralization._query_neo4j_clusters", return_value=([], False)):
            out = await dec.get_own_decentralization(_TOKEN, _CHAIN, wallet_map=unavailable_wm)
        assert out["result"] == "unavailable"
        assert out["decentralization_score"] is None
        assert out["decentralization_line"] == "\u2014"
        assert out["bubble_line"] == "\u2014"

    @pytest.mark.asyncio
    async def test_scanned_with_prefetched_payloads(self) -> None:
        holders = [_holder("0xbig", 40.0), _holder("0xa", 4.0), _holder("0xb", 2.0)]
        with patch("app.domains.scanner.engines.own_decentralization._query_neo4j_clusters", return_value=([], False)):
            out = await dec.get_own_decentralization(
                _TOKEN,
                _CHAIN,
                deployer=_DEPLOYER,
                wallet_map=_wallet_map(holders),
                cluster_analysis=_cluster(),
            )
        assert out["result"] == "scanned"
        assert out["source"] == "own"
        assert isinstance(out["decentralization_score"], int)
        assert "\U0001fae7 Decentralization:" in out["decentralization_line"]
        assert out["bubble_line"].startswith("\U0001fae7 ")
        assert out["breakdown"]["cluster_source"] == "clickhouse"

    @pytest.mark.asyncio
    async def test_neo4j_clusters_win_over_clickhouse(self) -> None:
        holders = [_holder("0xa", 30.0), _holder("0xb", 10.0), _holder("0xc", 2.0)]
        neo4j_clusters = [{"funder": "neo4j-0xroot1", "wallet_count": 2, "wallets": ["0xa", "0xb"], "source": "neo4j"}]
        with patch(
            "app.domains.scanner.engines.own_decentralization._query_neo4j_clusters",
            return_value=(neo4j_clusters, True),
        ):
            out = await dec.get_own_decentralization(
                _TOKEN,
                _CHAIN,
                wallet_map=_wallet_map(holders),
                cluster_analysis=_cluster(),
            )
        assert out["breakdown"]["cluster_source"] == "neo4j"
        assert out["breakdown"]["distinct_funders"] == 1
        assert out["breakdown"]["largest_cluster_size"] == 2

    @pytest.mark.asyncio
    async def test_never_raises_when_neo4j_query_blows_up(self) -> None:
        holders = [_holder("0xa", 5.0)]
        with patch(
            "app.domains.scanner.engines.own_decentralization._query_neo4j_clusters",
            side_effect=RuntimeError("neo4j down"),
        ):
            out = await dec.get_own_decentralization(_TOKEN, _CHAIN, wallet_map=_wallet_map(holders))
        assert out["result"] == "scanned"  # holder data still enough

    @pytest.mark.asyncio
    async def test_cache_serves_second_call(self) -> None:
        holders = [_holder("0xa", 5.0)]
        with patch("app.domains.scanner.engines.own_decentralization._query_neo4j_clusters", return_value=([], False)):
            first = await dec.get_own_decentralization(_TOKEN, _CHAIN, wallet_map=_wallet_map(holders))
            second = await dec.get_own_decentralization(_TOKEN, _CHAIN, wallet_map=_wallet_map(holders))
        assert second["result"] == "scanned"
        assert first["decentralization_score"] == second["decentralization_score"]

    def test_cache_stats_shape(self) -> None:
        stats = dec.get_cache_stats()
        assert stats["cache_size"] == 0
        assert stats["cache_max"] == 100
        assert stats["cache_ttl_seconds"] == 300
        assert stats["max_bubbles"] == 10
