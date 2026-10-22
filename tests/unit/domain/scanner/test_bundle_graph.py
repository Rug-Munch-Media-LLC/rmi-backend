"""Tests for the bundle graph engine (pure logic, no data source).

Covers the three things a bundle claim depends on: correct leg attribution,
co-timing grouping, and the multi-signal gate that refuses to call a single
heuristic a bundle.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.scanner.engines.bundle_graph import confidence, funding, size, timing
from app.domains.scanner.engines.bundle_graph.behaviour import (
    build_positions,
    first_minute_volume_usd,
)
from app.domains.scanner.engines.bundle_graph.build import build_bundle_graph

MINT = "Mint1111111111111111111111111111111111111111"
SOL = "So11111111111111111111111111111111111111112"


def _row(
    *, slot: int, ts: int, sender: str, recipient: str, mint_out: int = 0, mint_in: int = 0
) -> dict[str, Any]:
    """One dex_events-shaped row with the mint as token0."""
    return {
        "token0": MINT,
        "token1": SOL,
        "block_number": slot,
        "ts": ts,
        "sender": sender,
        "recipient": recipient,
        "amount0_in": str(mint_in),
        "amount1_in": "0",
        "amount0_out": str(mint_out),
        "amount1_out": "0",
        "price_usd": 0.001,
    }


def test_buy_leg_goes_to_recipient_and_sell_leg_to_sender() -> None:
    rows = [
        _row(slot=10, ts=1_000, sender="pool", recipient="alice", mint_out=1_000),
        _row(slot=11, ts=1_010, sender="bob", recipient="pool", mint_in=400),
    ]
    positions = build_positions(rows, MINT)

    assert positions["alice"].tokens_bought_raw == 1_000
    assert positions["alice"].buy_count == 1
    assert positions["bob"].tokens_sold_raw == 400
    assert positions["bob"].sell_count == 1
    assert positions["bob"].still_holding is False


def test_holder_kept_tokens_is_still_holding() -> None:
    rows = [
        _row(slot=10, ts=1_000, sender="pool", recipient="alice", mint_out=1_000),
        _row(slot=12, ts=1_020, sender="alice", recipient="pool", mint_in=100),
    ]
    positions = build_positions(rows, MINT)

    assert positions["alice"].net_tokens_raw == 900
    assert positions["alice"].still_holding is True
    assert positions["alice"].realised_usd == pytest.approx(0.1)


def test_first_minute_volume_excludes_late_legs() -> None:
    rows = [
        _row(slot=10, ts=1_000, sender="pool", recipient="alice", mint_out=1_000),
        _row(slot=99, ts=5_000, sender="pool", recipient="bob", mint_out=5_000),
    ]
    # 1000 * 0.001 = 1.0 USD inside the minute, the 5000-token leg is outside.
    assert first_minute_volume_usd(rows, MINT, 60) == pytest.approx(1.0)


def test_cluster_by_window_groups_only_tight_slots() -> None:
    rows = [
        _row(slot=100, ts=1_000, sender="p", recipient="a", mint_out=100),
        _row(slot=101, ts=1_001, sender="p", recipient="b", mint_out=100),
        _row(slot=102, ts=1_002, sender="p", recipient="c", mint_out=100),
        _row(slot=400, ts=2_000, sender="p", recipient="d", mint_out=100),
    ]
    positions = build_positions(rows, MINT)

    groups = timing.cluster_by_window(positions, window_slots=2)

    assert groups == [["a", "b", "c"]]
    assert timing.span_slots(positions, groups[0]) == 2


def test_single_family_never_reaches_bundle_tier() -> None:
    score, tier = confidence.score_confidence({"same_slot_buys", "early_slot_cluster"})

    assert tier == "weak"
    assert score <= 45
    assert confidence.is_bundle(tier) is False


def test_two_families_are_strong_three_confirmed() -> None:
    _, tier_two = confidence.score_confidence({"same_slot_buys", "shared_funder"})
    _, tier_three = confidence.score_confidence({"same_slot_buys", "shared_funder", "similar_size"})

    assert tier_two == "strong"
    assert tier_three == "confirmed"
    assert confidence.is_bundle(tier_three) is True


def test_tier_without_signals_is_none() -> None:
    assert confidence.score_confidence(set()) == (0, "none")


def test_similar_size_needs_three_wallets() -> None:
    rows = [
        _row(slot=100, ts=1_000, sender="p", recipient=f"w{i}", mint_out=1_000) for i in range(2)
    ]
    positions = build_positions(rows, MINT)

    assert size.similar_size(positions, list(positions)) is False


def test_cex_withdrawal_edge_inside_window(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [_row(slot=100, ts=1_000, sender="p", recipient="alice", mint_out=1_000)]
    positions = build_positions(rows, MINT)
    transfers = [
        {
            "from_address": "cexhot",
            "to_address": "alice",
            "amount": 10**9,
            "slot": 90,
            "ts": 950,
            "signature": "sig1",
        }
    ]
    monkeypatch.setattr(funding, "label_wallet", lambda addr, chain: {"type": "cex_hot"})

    edges = funding.value_edges(transfers, positions, "solana", launch_ts=1_000)

    assert [e.kind for e in edges] == ["cex_withdrawal"]
    assert edges[0].evidence_tx == "sig1"
    assert edges[0].to_address == "alice"


def test_shared_funder_group_requires_two_buyers(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _row(slot=100, ts=1_000, sender="p", recipient="alice", mint_out=1_000),
        _row(slot=100, ts=1_000, sender="p", recipient="bob", mint_out=1_000),
        _row(slot=100, ts=1_000, sender="p", recipient="carol", mint_out=1_000),
    ]
    positions = build_positions(rows, MINT)
    transfers = [
        {
            "from_address": "funder",
            "to_address": w,
            "amount": 10**9,
            "slot": 80,
            "ts": 900,
            "signature": f"s_{w}",
        }
        for w in ("alice", "bob")
    ]
    monkeypatch.setattr(funding, "label_wallet", lambda addr, chain: {"type": "unknown"})

    groups = funding.shared_funder_groups(
        funding.value_edges(transfers, positions, "solana", 1_000)
    )

    assert groups == {"funder": ["alice", "bob"]}


async def test_build_graph_returns_contract_when_no_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.domains.scanner.engines.bundle_graph.queries.fetch_launch_slot",
        lambda token, chain: None,
    )

    payload = await build_bundle_graph("NoDataMint", "solana")

    assert payload["wallets"] == []
    assert payload["edges"] == []
    assert payload["clusters"] == []
    assert payload["confidence"] == 0
    assert payload["confidence_tier"] == "none"
    assert payload["bundle_pct_of_supply"] is None
    assert payload["notes"]
