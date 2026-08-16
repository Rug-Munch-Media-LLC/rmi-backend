"""Unit tests for ``own_cluster_analysis`` — funding-source clusters + coordinated buys.

Covers the pure clustering / verdict logic and the fail-open ``unavailable``
payload (ClickHouse absent). No real ClickHouse — the query layer is patched.
"""

from __future__ import annotations

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
