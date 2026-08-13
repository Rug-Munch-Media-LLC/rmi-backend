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
from unittest.mock import AsyncMock

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
