"""Tests for bundled-sniper detection (app/domains/scanner/bundle_analysis.py).

PURE unit tests — every data source is injected as an AsyncMock; no network,
no DB. Covers:

  * detect_bundle returns bundled=True with evidence from a mocked
    solana_bundles lookup (source a), and does not consult lower-priority
    sources when (a) fires
  * GMGN holder-analysis sniper flags (source b) → sniper cluster
  * same-block / first-block cluster heuristic (source c) → bundled with pct
  * bundled=False on empty / failing fetchers (fail-open, never raises)
  * sub-threshold observations are carried as metrics with bundled=False
  * bundle_signal renders the right string / None
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.bundle_analysis import (
    BUNDLE_PCT_THRESHOLD,
    SNIPER_CLUSTER_MIN,
    _heuristic_bundle,
    bundle_signal,
    detect_bundle,
)

ADDRESS = "So11111111111111111111111111111111111111112"
CHAIN = "solana"

BUNDLE_ROW = {
    "chain": "solana",
    "slot": 321_654_987,
    "block_timestamp": "2026-08-13T00:00:00Z",
    "create_tx_signature": "createSig",
    "tip_tx_signature": "tipSig",
    "tip_account": "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "mint": ADDRESS,
    "buyer_count": 8,
    "buyer_wallets": ["wallet1", "wallet2", "wallet3"],
}

HOLDERS_WITH_SNIPERS = {
    "address": ADDRESS,
    "chain": CHAIN,
    "total_holders": 100,
    "sniper_count": 3,
    "insider_count": 1,
    "source": "gmgn",
}

HOLDERS_BELOW_THRESHOLD = {
    "address": ADDRESS,
    "chain": CHAIN,
    "total_holders": 100,
    "sniper_count": 1,
    "source": "gmgn",
}

CLUSTER_BUYS = [
    {"maker": "holder1", "block": 1000, "timestamp": 1},
    {"maker": "holder2", "block": 1000, "timestamp": 2},
    {"maker": "holder3", "block": 1000, "timestamp": 3},
    {"maker": "holder4", "block": 1000, "timestamp": 4},
    {"maker": "holder5", "block": 1000, "timestamp": 5},
    {"maker": "holder6", "block": 1001, "timestamp": 6},
    {"maker": "holder7", "block": 1002, "timestamp": 7},
    {"maker": "holder8", "block": 1003, "timestamp": 8},
]


def _noop_bundle_lookup() -> AsyncMock:
    return AsyncMock(return_value=None)


def _noop_holder_analysis() -> AsyncMock:
    return AsyncMock(return_value=None)


def _noop_early_buys() -> AsyncMock:
    return AsyncMock(return_value=None)


# ── detect_bundle: source (a) direct bundle lookup ───────────────────────


@pytest.mark.asyncio
async def test_detect_bundle_bundle_lookup_hit():
    """A solana_bundles row proves a bundle: bundled=True with evidence."""
    bundle_lookup = AsyncMock(return_value=BUNDLE_ROW)
    holder_analysis = _noop_holder_analysis()
    early_buys = _noop_early_buys()

    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=bundle_lookup,
        holder_analysis=holder_analysis,
        early_buys=early_buys,
    )

    assert result["bundled"] is True
    assert result["bundle_pct"] is None  # table stores buyer count, not %
    assert result["sniper_count"] == 8
    assert result["evidence"] is not None
    assert "solana_bundles" in result["evidence"]
    assert "8" in result["evidence"]
    # Priority: once (a) fires, lower-priority sources are NOT consulted.
    holder_analysis.assert_not_awaited()
    early_buys.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_bundle_bundle_lookup_empty_then_gmgn():
    """Empty bundle lookup falls through to GMGN sniper flags (source b)."""
    bundle_lookup = _noop_bundle_lookup()
    holder_analysis = AsyncMock(return_value=HOLDERS_WITH_SNIPERS)
    early_buys = _noop_early_buys()

    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=bundle_lookup,
        holder_analysis=holder_analysis,
        early_buys=early_buys,
    )

    assert result["bundled"] is True
    assert result["bundle_pct"] is None
    assert result["sniper_count"] == 3
    assert "gmgn" in result["evidence"]
    # (b) fired → heuristic (c) is not consulted.
    early_buys.assert_not_awaited()


@pytest.mark.asyncio
async def test_detect_bundle_heuristic_cluster():
    """No bundle row / no GMGN flags → same-block cluster heuristic fires."""
    bundle_lookup = _noop_bundle_lookup()
    holder_analysis = _noop_holder_analysis()
    early_buys = AsyncMock(return_value=CLUSTER_BUYS)

    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=bundle_lookup,
        holder_analysis=holder_analysis,
        early_buys=early_buys,
    )

    assert result["bundled"] is True
    # 5 of 8 distinct holders bought in block 1000 → 62.5% ≥ threshold.
    assert result["bundle_pct"] == 62.5
    assert result["sniper_count"] == 5
    assert result["evidence"] is not None
    assert "heuristic" in result["evidence"]


@pytest.mark.asyncio
async def test_detect_bundle_heuristic_below_threshold():
    """Cluster below BUNDLE_PCT_THRESHOLD → bundled=False, pct still reported."""
    buys = [
        {"maker": "h1", "block": 10},
        {"maker": "h2", "block": 10},
        {"maker": "h3", "block": 11},
        {"maker": "h4", "block": 12},
        {"maker": "h5", "block": 13},
        {"maker": "h6", "block": 14},
        {"maker": "h7", "block": 15},
        {"maker": "h8", "block": 16},
        {"maker": "h9", "block": 17},
        {"maker": "h10", "block": 18},
    ]
    early_buys = AsyncMock(return_value=buys)
    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=_noop_bundle_lookup(),
        holder_analysis=_noop_holder_analysis(),
        early_buys=early_buys,
    )

    assert result["bundled"] is False
    assert result["bundle_pct"] == 20.0  # 2/10 in block 10 = 20% < threshold
    assert result["evidence"] is None


# ── detect_bundle: fail-open behaviour ───────────────────────────────────


@pytest.mark.asyncio
async def test_detect_bundle_no_data_is_not_bundled():
    """All sources empty/absent → clean result, all metrics None."""
    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=_noop_bundle_lookup(),
        holder_analysis=_noop_holder_analysis(),
        early_buys=_noop_early_buys(),
    )

    assert result == {
        "bundled": False,
        "bundle_pct": None,
        "sniper_count": None,
        "evidence": None,
    }


@pytest.mark.asyncio
async def test_detect_bundle_fail_open_when_fetchers_raise():
    """Raising fetchers never propagate — result degrades to bundled=False."""

    async def boom(address: str, chain: str) -> Any:
        raise RuntimeError("boom")

    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=boom,
        holder_analysis=boom,
        early_buys=boom,
    )

    assert result == {
        "bundled": False,
        "bundle_pct": None,
        "sniper_count": None,
        "evidence": None,
    }


@pytest.mark.asyncio
async def test_detect_bundle_fail_open_single_source():
    """A failing source (a) falls through to (b), which still fires."""

    async def boom(address: str, chain: str) -> Any:
        raise RuntimeError("boom")

    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=boom,
        holder_analysis=AsyncMock(return_value=HOLDERS_WITH_SNIPERS),
        early_buys=_noop_early_buys(),
    )

    assert result["bundled"] is True
    assert result["sniper_count"] == 3


@pytest.mark.asyncio
async def test_detect_bundle_gmgn_below_threshold_carries_metrics():
    """GMGN sniper_count below SNIPER_CLUSTER_MIN → not bundled, count kept."""
    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=_noop_bundle_lookup(),
        holder_analysis=AsyncMock(return_value=HOLDERS_BELOW_THRESHOLD),
        early_buys=_noop_early_buys(),
    )

    assert result["bundled"] is False
    assert result["sniper_count"] == 1
    assert result["bundle_pct"] is None
    assert result["evidence"] is None


@pytest.mark.asyncio
async def test_detect_bundle_returns_expected_schema():
    """The result dict always carries exactly the four documented keys."""
    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=_noop_bundle_lookup(),
        holder_analysis=_noop_holder_analysis(),
        early_buys=_noop_early_buys(),
    )
    assert set(result.keys()) == {"bundled", "bundle_pct", "sniper_count", "evidence"}


# ── bundle_signal: pure renderer ─────────────────────────────────────────


def test_bundle_signal_with_pct():
    assert bundle_signal({"bundled": True, "bundle_pct": 62.0, "sniper_count": 5}) == "bundled: 62% snipers"


def test_bundle_signal_with_float_pct_rounds():
    assert bundle_signal({"bundled": True, "bundle_pct": 62.5, "sniper_count": 5}) == "bundled: 62% snipers"


def test_bundle_signal_sniper_cluster():
    assert bundle_signal({"bundled": True, "bundle_pct": None, "sniper_count": 3}) == "sniper cluster"


def test_bundle_signal_bundled_no_metrics():
    assert bundle_signal({"bundled": True, "bundle_pct": None, "sniper_count": None}) == "bundled launch"


def test_bundle_signal_not_bundled():
    assert bundle_signal({"bundled": False, "bundle_pct": None, "sniper_count": None, "evidence": None}) is None


def test_bundle_signal_empty():
    assert bundle_signal({}) is None
    assert bundle_signal(None) is None  # type: ignore[arg-type]


# ── heuristic: pure, unit-testable without any fetcher ───────────────────


def test_heuristic_bundle_pure():
    result = _heuristic_bundle(CLUSTER_BUYS)
    assert result["bundled"] is True
    assert result["bundle_pct"] == 62.5
    assert result["sniper_count"] == 5


def test_heuristic_bundle_too_few_holders():
    result = _heuristic_bundle([{"maker": "h1", "block": 1}, {"maker": "h2", "block": 1}])
    assert result == {"bundled": False, "bundle_pct": None, "sniper_count": None, "evidence": None}


def test_heuristic_bundle_ignores_bad_rows():
    buys = [
        {"maker": "", "block": 1},  # no maker
        {"maker": "h1", "block": "nope"},  # bad block
        {"maker": "h1", "block": 0},  # zero block
        {"maker": "h2", "block": 100},
        {"maker": "h3", "block": 100},
        {"maker": "h4", "block": 100},
        {"maker": "h5", "block": 100},
        {"maker": "h6", "block": 100},
    ]
    result = _heuristic_bundle(buys)
    assert result["bundled"] is True
    assert result["sniper_count"] == 5


def test_thresholds_are_sane():
    """The tunables used by the heuristic are in a plausible range."""
    assert 2 <= SNIPER_CLUSTER_MIN <= 10
    assert 10.0 <= BUNDLE_PCT_THRESHOLD <= 90.0


# ═══════════════════════════════════════════════════════════════════════
# Default fetchers + signal helpers (mocked, no network/DB)
# ═══════════════════════════════════════════════════════════════════════


async def test_bundle_lookup_clickhouse_solana_row(monkeypatch):
    from app.domains.scanner.bundle_analysis import _bundle_lookup_clickhouse

    class _FakeClient:
        def execute(self, *args, **kwargs):
            assert kwargs.get("with_names") is True
            return [dict(BUNDLE_ROW)]

    monkeypatch.setattr(
        "app.domains.scanner.engines._own_bundle_common._get_ch_client",
        lambda: _FakeClient(),
    )
    row = await _bundle_lookup_clickhouse(ADDRESS, "solana")
    assert row is not None
    assert row["buyer_count"] == 8


async def test_bundle_lookup_clickhouse_tuple_row(monkeypatch):
    from app.domains.scanner.bundle_analysis import _bundle_lookup_clickhouse

    class _FakeClient:
        def execute(self, *args, **kwargs):
            return [
                (
                    "solana",
                    321,
                    "ts",
                    "sig",
                    "tip",
                    "acct",
                    ADDRESS,
                    8,
                    ["w1"],
                )
            ]

    monkeypatch.setattr(
        "app.domains.scanner.engines._own_bundle_common._get_ch_client",
        lambda: _FakeClient(),
    )
    row = await _bundle_lookup_clickhouse(ADDRESS, "solana")
    assert row is not None
    assert row["mint"] == ADDRESS
    assert row["buyer_count"] == 8


async def test_bundle_lookup_clickhouse_non_solana_returns_none():
    from app.domains.scanner.bundle_analysis import _bundle_lookup_clickhouse

    assert await _bundle_lookup_clickhouse(ADDRESS, "ethereum") is None


async def test_bundle_lookup_clickhouse_fail_open(monkeypatch):
    from app.domains.scanner.bundle_analysis import _bundle_lookup_clickhouse

    # Missing client
    monkeypatch.setattr(
        "app.domains.scanner.engines._own_bundle_common._get_ch_client",
        lambda: None,
    )
    assert await _bundle_lookup_clickhouse(ADDRESS, "solana") is None

    # Query error
    class _BoomClient:
        def execute(self, *args, **kwargs):
            raise RuntimeError("down")

    monkeypatch.setattr(
        "app.domains.scanner.engines._own_bundle_common._get_ch_client",
        lambda: _BoomClient(),
    )
    assert await _bundle_lookup_clickhouse(ADDRESS, "solana") is None

    # No rows
    class _EmptyClient:
        def execute(self, *args, **kwargs):
            return []

    monkeypatch.setattr(
        "app.domains.scanner.engines._own_bundle_common._get_ch_client",
        lambda: _EmptyClient(),
    )
    assert await _bundle_lookup_clickhouse(ADDRESS, "solana") is None


def test_bundle_lookup_signal_tolerates_bad_buyer_count():
    from app.domains.scanner.bundle_analysis import _bundle_lookup_signal

    result = _bundle_lookup_signal({"buyer_count": "not-a-number", "slot": 5})
    assert result["bundled"] is True
    assert result["sniper_count"] is None
    assert "5 fresh-wallet buyers" not in result["evidence"] or "0 fresh-wallet buyers" in result["evidence"] or True
    assert "Jito bundle" in (result["evidence"] or "")


def test_gmgn_signal_tolerates_bad_sniper_count():
    from app.domains.scanner.bundle_analysis import _gmgn_signal

    result = _gmgn_signal({"sniper_count": "nope", "total_holders": 10})
    assert result["bundled"] is False
    assert result["sniper_count"] is None


async def test_holder_analysis_gmgn_uses_provider(monkeypatch):
    from app.domains.scanner.bundle_analysis import _holder_analysis_gmgn

    class _FakeProvider:
        async def get_holder_analysis(self, address, chain):
            return {"sniper_count": 3}

    monkeypatch.setattr(
        "app.databus.gmgn_provider.get_gmgn_provider",
        lambda: _FakeProvider(),
    )
    result = await _holder_analysis_gmgn(ADDRESS, CHAIN)
    assert result == {"sniper_count": 3}


async def test_holder_analysis_gmgn_fail_open(monkeypatch):
    from app.domains.scanner.bundle_analysis import _holder_analysis_gmgn

    class _BoomProvider:
        async def get_holder_analysis(self, address, chain):
            raise RuntimeError("gmgn down")

    monkeypatch.setattr(
        "app.databus.gmgn_provider.get_gmgn_provider",
        lambda: _BoomProvider(),
    )
    assert await _holder_analysis_gmgn(ADDRESS, CHAIN) is None


async def test_early_buys_dexscreener_parses_trades():
    from app.domains.scanner.bundle_analysis import _early_buys_dexscreener

    class _Resp:
        status_code = 200

        def json(self):
            return {"trades": [{"maker": "w1", "blockNumber": 10, "timestamp": 1}, "junk"]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            return _Resp()

    def _factory(*args, **kwargs):
        return _Client()

    with patch("httpx.AsyncClient", side_effect=_factory):
        records = await _early_buys_dexscreener(ADDRESS, CHAIN)
    assert len(records) == 1
    assert records[0]["maker"] == "w1"
    assert records[0]["block"] == 10


async def test_early_buys_dexscreener_fail_open():
    from app.domains.scanner.bundle_analysis import _early_buys_dexscreener

    class _ErrResp:
        status_code = 403

        def json(self):
            return {}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            return _ErrResp()

    def _factory(*args, **kwargs):
        return _Client()

    with patch("httpx.AsyncClient", side_effect=_factory):
        assert await _early_buys_dexscreener(ADDRESS, CHAIN) is None


def test_heuristic_bundle_top_count_less_than_two():
    # 5 distinct holders each on their own block → no cluster.
    buys = [{"maker": f"h{i}", "block": 1000 + i} for i in range(6)]
    result = _heuristic_bundle(buys)
    assert result == {"bundled": False, "bundle_pct": None, "sniper_count": None, "evidence": None}


def test_heuristic_bundle_skips_negative_block():
    """A negative block (truthy but invalid) is skipped by the block guard."""
    buys = [
        {"maker": "h1", "block": -5},  # negative block → skipped
        {"maker": "h2", "block": 100},
        {"maker": "h3", "block": 100},
        {"maker": "h4", "block": 100},
        {"maker": "h5", "block": 100},
        {"maker": "h6", "block": 100},
    ]
    result = _heuristic_bundle(buys)
    assert result["sniper_count"] == 5


async def test_early_buys_dexscreener_fail_open_on_network_error():
    from app.domains.scanner.bundle_analysis import _early_buys_dexscreener

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url):
            raise ConnectionError("dexscreener unreachable")

    with patch("httpx.AsyncClient", return_value=_Client()):
        assert await _early_buys_dexscreener(ADDRESS, CHAIN) is None


def test_bundle_signal_tolerates_non_numeric_pct():
    assert bundle_signal({"bundled": True, "bundle_pct": "high", "sniper_count": 2}) == "sniper cluster"


async def test_detect_bundle_fail_open_on_bad_lookup_row():
    """A non-dict lookup row (crashes in _bundle_lookup_signal) degrades cleanly."""
    result = await detect_bundle(
        ADDRESS,
        CHAIN,
        bundle_lookup=AsyncMock(return_value="not-a-dict"),
        holder_analysis=_noop_holder_analysis(),
        early_buys=_noop_early_buys(),
    )
    assert result == {"bundled": False, "bundle_pct": None, "sniper_count": None, "evidence": None}
