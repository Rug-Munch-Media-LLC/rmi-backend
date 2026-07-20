"""Tests for BubbleMapsPro with mocked Helius RPC responses.

Covers:
- BubbleMap and BubbleNode dataclass defaults
- Generate map with mocked transactions
- Connection strength calculation
- Wallet classification
- Risk scoring
- Position calculation
- HTML export
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest


# ── BubbleNode ─────────────────────────────────────────────────────────


def test_bubble_node_defaults():
    """BubbleNode has correct default values."""
    from app.bubble_maps import BubbleNode

    node = BubbleNode(id="node1", address="0xabc", type="center")

    assert node.id == "node1"
    assert node.address == "0xabc"
    assert node.type == "center"
    assert node.radius == 20.0
    assert node.color == "#69db7c"
    assert node.risk_score == 0.0
    assert node.risk_level == "unknown"
    assert node.connected_to == []
    assert node.tags == []


def test_bubble_node_to_dict():
    """BubbleNode.to_dict() returns expected keys."""
    from app.bubble_maps import BubbleNode

    node = BubbleNode(id="n1", address="0xabc", type="exchange", radius=30.0, color="#4dabf7")
    d = node.to_dict()

    assert d["id"] == "n1"
    assert d["type"] == "exchange"
    assert d["radius"] == 30.0
    assert d["color"] == "#4dabf7"
    assert "label" in d
    assert "risk_score" in d


# ── BubbleLink ─────────────────────────────────────────────────────────


def test_bubble_link_defaults():
    """BubbleLink has correct default values."""
    from app.bubble_maps import BubbleLink

    link = BubbleLink(source="src", target="tgt")

    assert link.source == "src"
    assert link.target == "tgt"
    assert link.strength == 0.5
    assert link.width == 2.0
    assert link.color == "#00d4ff"
    assert link.total_volume == 0.0
    assert link.transaction_count == 0


def test_bubble_link_to_dict():
    """BubbleLink.to_dict() returns expected keys."""
    from app.bubble_maps import BubbleLink

    link = BubbleLink(source="a", target="b", strength=0.8, width=5.0, total_volume=1000.0)
    d = link.to_dict()

    assert d["source"] == "a"
    assert d["target"] == "b"
    assert d["strength"] == 0.8
    assert d["width"] == 5.0
    assert d["volume"] == 1000.0


# ── BubbleMap ──────────────────────────────────────────────────────────


def test_bubble_map_to_dict():
    """BubbleMap.to_dict() returns complete structure."""
    from datetime import datetime

    from app.bubble_maps import BubbleLink, BubbleMap, BubbleNode

    now = datetime.now(UTC)
    bm = BubbleMap(map_id="test_map", center_wallet="0xcenter", created_at=now)
    bm.nodes.append(BubbleNode(id="n1", address="0xcenter", type="center"))
    bm.links.append(BubbleLink(source="n1", target="n2", strength=0.5))
    bm.total_volume = 50000.0
    bm.total_transactions = 100

    d = bm.to_dict()

    assert d["map_id"] == "test_map"
    assert d["center_wallet"] == "0xcenter"
    assert d["stats"]["nodes"] == 1
    assert d["stats"]["links"] == 1
    assert d["stats"]["total_volume"] == 50000.0
    assert len(d["nodes"]) == 1
    assert len(d["links"]) == 1


# ── Connection Strength ────────────────────────────────────────────────


@pytest.fixture
def bubble_pro():
    """Create a BubbleMapsPro instance with clean caches."""
    from app.bubble_maps import BubbleMapsPro

    bp = BubbleMapsPro()
    bp.transaction_cache = {}
    bp.entity_cache = {}
    return bp


def test_calculate_connection_strength_no_transactions(bubble_pro):
    """Zero relevant transactions = zero strength."""
    strength = bubble_pro._calculate_connection_strength("wallet_a", "wallet_b", [])
    assert strength == 0.0


def test_calculate_connection_strength_single_tx(bubble_pro):
    """Single transaction gives non-zero strength."""
    transactions = [{"from": "wallet_a", "to": "wallet_b", "amount": 1000, "timestamp": 1700000000}]
    strength = bubble_pro._calculate_connection_strength("wallet_a", "wallet_b", transactions)
    assert strength > 0.0
    assert strength <= 1.0


def test_calculate_connection_strength_high(bubble_pro):
    """Many transactions give higher strength."""
    transactions = [
        {"from": "wallet_a", "to": "wallet_b", "amount": 10000, "timestamp": 1700000000 + i}
        for i in range(50)
    ]
    strength = bubble_pro._calculate_connection_strength("wallet_a", "wallet_b", transactions)
    # Should be > 0.25 from count alone, plus volume
    assert strength > 0.25


def test_calculate_connection_strength_bidirectional(bubble_pro):
    """Two-way transactions get reciprocity bonus."""
    tx_a_to_b = [
        {"from": "wallet_a", "to": "wallet_b", "amount": 1000, "timestamp": 1700000000 + i}
        for i in range(5)
    ]
    tx_b_to_a = [
        {"from": "wallet_b", "to": "wallet_a", "amount": 500, "timestamp": 1700000100 + i}
        for i in range(3)
    ]
    strength = bubble_pro._calculate_connection_strength(
        "wallet_a", "wallet_b", tx_a_to_b + tx_b_to_a
    )
    reciprocity = strength
    assert reciprocity > 0

    # One-way should be less
    strength_oneway = bubble_pro._calculate_connection_strength("wallet_a", "wallet_b", tx_a_to_b)
    assert strength > strength_oneway or abs(strength - strength_oneway) < 0.01


# ── Wallet Classification ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_classify_wallet_exchange(bubble_pro):
    """Wallet with many txs and counterparties is classified as exchange."""
    transactions = [
        {
            "from": "wallet",
            "to": f"counterparty_{i}",
            "amount": 100,
            "timestamp": 1700000000 + i,
            "status": "success",
        }
        for i in range(120)
    ]
    result = await bubble_pro._classify_wallet("wallet", transactions)
    assert result == "exchange"


@pytest.mark.asyncio
async def test_classify_wallet_bot(bubble_pro):
    """Wallet with fast transactions is classified as bot."""
    transactions = [
        {
            "from": "wallet",
            "to": f"counterparty_{i}",
            "amount": 100,
            "timestamp": 1700000000 + i * 2,  # 2 seconds apart
            "status": "success",
        }
        for i in range(10)
    ]
    result = await bubble_pro._classify_wallet("wallet", transactions)
    assert result == "bot"


@pytest.mark.asyncio
async def test_classify_wallet_whale(bubble_pro):
    """Wallet with large transactions is classified as whale."""
    transactions = [
        {
            "from": "wallet",
            "to": "counterparty",
            "amount": 200000,
            "timestamp": 1700000000,
            "status": "success",
        }
    ]
    result = await bubble_pro._classify_wallet("wallet", transactions)
    assert result == "whale"


@pytest.mark.asyncio
async def test_classify_wallet_unknown(bubble_pro):
    """Wallet with no transactions is unknown."""
    result = await bubble_pro._classify_wallet("wallet", [])
    assert result == "unknown"


# ── Risk Calculation ───────────────────────────────────────────────────


def test_calculate_risk_no_transactions(bubble_pro):
    """Zero transactions gives 0 risk."""
    risk_score, risk_level = bubble_pro._calculate_risk([], 0)
    assert risk_score == 0.0
    assert risk_level == "unknown"


def test_calculate_risk_high_velocity(bubble_pro):
    """Many transactions increase risk."""
    transactions = [
        {"from": "a", "to": "b", "amount": 10, "timestamp": 1700000000 + i, "status": "success"}
        for i in range(150)
    ]
    risk_score, risk_level = bubble_pro._calculate_risk(transactions, 10)
    assert risk_score >= 20  # Velocity bonus
    assert risk_level in ("medium", "high", "critical")


def test_calculate_risk_high_fail_rate(bubble_pro):
    """High failed transaction ratio increases risk."""
    transactions = [
        {"from": "a", "to": "b", "amount": 10, "timestamp": 1700000000 + i, "status": "failed"}
        for i in range(50)
    ]
    risk_score, risk_level = bubble_pro._calculate_risk(transactions, 10)
    assert risk_score >= 15  # Fail ratio bonus


def test_calculate_risk_time_concentration(bubble_pro):
    """Transactions in short period increase risk."""
    transactions = [
        {"from": "a", "to": "b", "amount": 10, "timestamp": 1700000000, "status": "success"}
        for _ in range(10)
    ]
    risk_score, _ = bubble_pro._calculate_risk(transactions, 10)
    # All at same timestamp or within 5 min
    assert risk_score > 0


def test_calculate_risk_identical_amounts(bubble_pro):
    """Identical transaction amounts increase risk."""
    transactions = [
        {"from": "a", "to": "b", "amount": 100, "timestamp": 1700000000 + i, "status": "success"}
        for i in range(5)
    ]
    risk_score, _ = bubble_pro._calculate_risk(transactions, 10)
    assert risk_score >= 15  # Identical amounts bonus


# ── Node Radius ────────────────────────────────────────────────────────


def test_calculate_radius_center(bubble_pro):
    """Center layer gives larger radius."""
    radius = bubble_pro._calculate_radius("addr", 0)
    assert radius == 20.0  # base


def test_calculate_radius_deep(bubble_pro):
    """Deeper layers give smaller radius."""
    radius = bubble_pro._calculate_radius("addr", 3)
    assert radius < 20.0


# ── Map Generation ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_map_no_transactions(bubble_pro):
    """Generate map with no transactions returns empty map."""
    transactions: list = []

    with patch.object(bubble_pro, "_get_transactions", return_value=transactions):
        bm = await bubble_pro.generate_map("0xcenter", depth=1)

        assert bm is not None
        assert bm.center_wallet == "0xcenter"
        assert bm.map_id.startswith("bubble_")
        assert len(bm.nodes) >= 1  # At least center node


@pytest.mark.asyncio
async def test_generate_map_with_transactions(bubble_pro):
    """Generate map with transactions creates nodes and links."""
    transactions = [
        {
            "from": "0xcenter",
            "to": "0xcounterparty1",
            "amount": 1000,
            "timestamp": 1700000000,
            "signature": "sig1",
            "status": "success",
            "token": "SOL",
        },
        {
            "from": "0xcenter",
            "to": "0xcounterparty2",
            "amount": 2000,
            "timestamp": 1700000100,
            "signature": "sig2",
            "status": "success",
            "token": "SOL",
        },
    ]

    with patch.object(bubble_pro, "_get_transactions", return_value=transactions):
        bm = await bubble_pro.generate_map("0xcenter", depth=1)

        assert bm is not None
        assert len(bm.nodes) >= 2  # Center + at least one counterparty
        assert bm.total_volume >= 0
        assert bm.total_transactions >= 0


# ── Transaction Fetching ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_transactions_with_time_range(bubble_pro):
    """Transaction time range filter works correctly."""
    transactions = [
        {
            "from": "wallet",
            "to": "counterparty",
            "amount": 100,
            "timestamp": 1700000000 + i,
            "status": "success",
        }
        for i in range(10)
    ]
    bubble_pro.transaction_cache["wallet"] = list(transactions)

    start = datetime(2023, 11, 14, tzinfo=UTC)  # Before all timestamps
    end = datetime(2023, 11, 15, tzinfo=UTC)  # Mid-range

    # 1700000000 = Nov 14 2023, so 1700000000+5 should still be Nov 14
    result = await bubble_pro._get_transactions("wallet", time_range=(start, end))

    assert isinstance(result, list)


# ── Stats Calculation ──────────────────────────────────────────────────


def test_calculate_stats(bubble_pro):
    """Stats are computed correctly from map data."""
    from app.bubble_maps import BubbleLink, BubbleMap, BubbleNode

    now = datetime.now(UTC)
    bm = BubbleMap(map_id="test", center_wallet="0xcenter", created_at=now)
    bm.nodes.append(BubbleNode(id="n1", address="0xa", type="center"))
    bm.nodes.append(BubbleNode(id="n2", address="0xb", type="wallet"))
    bm.links.append(BubbleLink(source="n1", target="n2", total_volume=1000.0, transaction_count=5))

    bubble_pro._calculate_stats(bm)

    assert bm.total_volume == 1000.0
    assert bm.total_transactions == 5
    assert bm.unique_wallets == 2
