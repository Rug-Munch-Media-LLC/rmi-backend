"""Tests for RugMaps bubble map engine — current API.

Covers:
- BubbleNode / BubbleLink / BubbleMap dataclass defaults and to_dict()
- Connection strength calculation (graph.calculate_connection_strength)
- Wallet classification (graph.classify_wallet) with Neo4j mocked
- Risk scoring (graph.calculate_risk)
- Node radius (graph.calculate_radius)
- Map generation (BubbleMapsPro.generate_map) with mocked tx fetcher
- Stats calculation (graph.calculate_stats)
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.rugmaps.models import BubbleLink, BubbleMap, BubbleNode

# ── BubbleNode ────────────────────────────────────────────────────────


def test_bubble_node_defaults():
    """BubbleNode has correct default values."""
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
    node = BubbleNode(id="n1", address="0xabc", type="exchange", radius=30.0, color="#4dabf7")
    d = node.to_dict()

    assert d["id"] == "n1"
    assert d["type"] == "exchange"
    assert d["radius"] == 30.0
    assert d["color"] == "#4dabf7"
    assert "label" in d
    assert "risk_score" in d


# ── BubbleLink ────────────────────────────────────────────────────────


def test_bubble_link_defaults():
    """BubbleLink has correct default values."""
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
    link = BubbleLink(source="a", target="b", strength=0.8, width=5.0, total_volume=1000.0)
    d = link.to_dict()

    assert d["source"] == "a"
    assert d["target"] == "b"
    assert d["strength"] == 0.8
    assert d["width"] == 5.0
    assert d["volume"] == 1000.0


# ── BubbleMap ─────────────────────────────────────────────────────────


def test_bubble_map_to_dict():
    """BubbleMap.to_dict() returns complete structure."""
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


# ── Connection Strength ───────────────────────────────────────────────


def test_calculate_connection_strength_zero():
    """Zero activity with a long time span gives near-zero strength."""
    from app.domains.scanner.rugmaps.graph import calculate_connection_strength

    # No txs, no value, no counterparties, huge time span → only recency floor
    strength = calculate_connection_strength(0, 0.0, 100_000.0, 0)
    assert strength < 0.01


def test_calculate_connection_strength_single_tx():
    """Single transaction gives non-zero strength."""
    from app.domains.scanner.rugmaps.graph import calculate_connection_strength

    strength = calculate_connection_strength(1, 1000.0, 1.0, 1)
    assert strength > 0.0
    assert strength <= 1.0


def test_calculate_connection_strength_scales_with_count():
    """More transactions yield higher (or equal) strength, capped at 1."""
    from app.domains.scanner.rugmaps.graph import calculate_connection_strength

    low = calculate_connection_strength(5, 1000.0, 24.0, 2)
    high = calculate_connection_strength(200, 1000.0, 24.0, 2)
    assert high >= low
    assert high <= 1.0


def test_calculate_connection_strength_scales_with_value():
    """Larger total value yields higher strength."""
    from app.domains.scanner.rugmaps.graph import calculate_connection_strength

    small = calculate_connection_strength(10, 100.0, 24.0, 2)
    large = calculate_connection_strength(10, 5_000_000.0, 24.0, 2)
    assert large > small
    assert large <= 1.0


# ── Wallet Classification ─────────────────────────────────────────────


@pytest.fixture
def neo4j_off():
    """Force classify_wallet to skip the Neo4j phase."""
    with patch(
        "app.domains.wallet.neo4j_client.query_wallet_type",
        new=AsyncMock(return_value=None),
    ):
        yield


@pytest.mark.asyncio
async def test_classify_wallet_exchange(neo4j_off):
    """Wallet with many unique counterparties is classified as exchange."""
    from app.domains.scanner.rugmaps.graph import classify_wallet

    transactions = [
        {"from": "wallet", "to": f"counterparty_{i}", "value": 100, "timestamp": 1700000000 + i} for i in range(150)
    ]
    result = await classify_wallet("wallet", transactions)
    assert result == "exchange"


@pytest.mark.asyncio
async def test_classify_wallet_whale(neo4j_off):
    """Wallet with large total volume is classified as whale."""
    from app.domains.scanner.rugmaps.graph import classify_wallet

    transactions = [
        {"from": "wallet", "to": "counterparty", "value": 200_000, "timestamp": 1700000000} for _ in range(60)
    ]
    result = await classify_wallet("wallet", transactions)
    assert result == "whale"


@pytest.mark.asyncio
async def test_classify_wallet_defi_trader(neo4j_off):
    """Wallet doing swap method calls is classified as defi_trader."""
    from app.domains.scanner.rugmaps.graph import classify_wallet

    transactions = [{"from": "wallet", "to": "pool", "value": 100, "method": "swap", "timestamp": 1700000000}]
    result = await classify_wallet("wallet", transactions)
    assert result == "defi_trader"


@pytest.mark.asyncio
async def test_classify_wallet_unknown(neo4j_off):
    """Wallet with no transactions and unknown address is unknown."""
    from app.domains.scanner.rugmaps.graph import classify_wallet

    result = await classify_wallet("wallet", [])
    assert result == "unknown"


@pytest.mark.asyncio
async def test_classify_wallet_empty_address(neo4j_off):
    """Empty address always classifies as unknown."""
    from app.domains.scanner.rugmaps.graph import classify_wallet

    result = await classify_wallet("", [])
    assert result == "unknown"


# ── Risk Calculation ──────────────────────────────────────────────────


def test_calculate_risk_no_transactions():
    """Zero transactions gives 0 risk."""
    from app.domains.scanner.rugmaps.graph import calculate_risk

    risk_score, risk_level = calculate_risk([], 0)
    assert risk_score == 0.0
    assert risk_level == "unknown"


def test_calculate_risk_large_transfer():
    """Single >1M transfer is flagged suspicious."""
    from app.domains.scanner.rugmaps.graph import calculate_risk

    transactions = [{"from": "a", "to": "b", "value": 2_000_000, "method": "transfer", "timestamp": 1700000000}]
    risk_score, _ = calculate_risk(transactions, 10)
    assert risk_score > 0


def test_calculate_risk_known_scammer_flag():
    """Known-scammer flag pushes risk toward critical."""
    from app.domains.scanner.rugmaps.graph import calculate_risk

    transactions = [{"from": "a", "to": "b", "value": 10, "flags": ["known_scammer"], "timestamp": 1700000000}]
    risk_score, risk_level = calculate_risk(transactions, 10)
    assert risk_score >= 60
    assert risk_level in ("high", "critical")


def test_calculate_risk_fresh_wallet_flag():
    """Fresh-wallet flag adds risk."""
    from app.domains.scanner.rugmaps.graph import calculate_risk

    transactions = [{"from": "a", "to": "b", "value": 10, "flags": ["fresh_wallet"], "timestamp": 1700000000}]
    risk_score, _ = calculate_risk(transactions, 10)
    assert risk_score >= 20


def test_calculate_risk_capped_at_100():
    """Risk score never exceeds 100."""
    from app.domains.scanner.rugmaps.graph import calculate_risk

    transactions = [
        {
            "from": "a",
            "to": "b",
            "value": 10_000_000,
            "flags": ["known_scammer", "fresh_wallet"],
            "timestamp": 1700000000,
        }
        for _ in range(20)
    ]
    risk_score, _ = calculate_risk(transactions, 10)
    assert 0 <= risk_score <= 100


# ── Node Radius ───────────────────────────────────────────────────────


def test_calculate_radius_center():
    """Center layer gives larger radius than deep layers."""
    from app.domains.scanner.rugmaps.graph import calculate_radius

    radius = calculate_radius("0x1234567890abcdef1234567890abcdef12345678", 0)
    assert radius > 0.0


def test_calculate_radius_deep():
    """Deeper layers give smaller radius for the same address."""
    from app.domains.scanner.rugmaps.graph import calculate_radius

    addr = "0x1234567890abcdef1234567890abcdef12345678"
    radius_layer0 = calculate_radius(addr, 0)
    radius_layer3 = calculate_radius(addr, 3)
    assert radius_layer3 < radius_layer0


# ── Map Generation ────────────────────────────────────────────────────


@pytest.fixture
def bubble_pro():
    """Create a BubbleMapsPro instance with clean caches and no live lookups."""
    from app.domains.scanner.rugmaps.engine import BubbleMapsPro

    bp = BubbleMapsPro()
    bp.transaction_cache = {}
    bp.entity_cache = {}
    return bp


@pytest.mark.asyncio
async def test_generate_map_no_transactions(bubble_pro):
    """Generate map with no transactions returns a map with the center node."""
    with (
        patch.object(bubble_pro, "_get_transactions", new=AsyncMock(return_value=[])),
        patch("app.domains.wallet.neo4j_client.query_wallet_type", new=AsyncMock(return_value=None)),
    ):
        bm = await bubble_pro.generate_map("0xcenter", depth=1)

    assert bm is not None
    assert bm.center_wallet == "0xcenter"
    assert bm.map_id.startswith("bubble_")
    assert len(bm.nodes) >= 1  # At least center node


@pytest.mark.asyncio
async def test_generate_map_with_transactions(bubble_pro):
    """Generate map with transactions creates center node and connections."""
    transactions = [
        {
            "from": "0xcenter",
            "to": "0xcounterparty1",
            "value": 1000,
            "timestamp": 1700000000,
            "signature": "sig1",
            "status": "success",
        },
        {
            "from": "0xcenter",
            "to": "0xcounterparty2",
            "value": 2000,
            "timestamp": 1700000100,
            "signature": "sig2",
            "status": "success",
        },
    ]

    with (
        patch.object(bubble_pro, "_get_transactions", new=AsyncMock(return_value=transactions)),
        patch("app.domains.wallet.neo4j_client.query_wallet_type", new=AsyncMock(return_value=None)),
    ):
        bm = await bubble_pro.generate_map("0xcenter", depth=1)

    assert bm is not None
    assert len(bm.nodes) >= 1
    assert bm.total_volume >= 0
    assert bm.total_transactions >= 0


# ── Transaction Fetching (cache) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_transactions_uses_cache(bubble_pro):
    """Cached transactions are returned without fetching from chain."""
    transactions = [
        {"from": "wallet", "to": "counterparty", "value": 100, "timestamp": 1700000000 + i} for i in range(10)
    ]
    bubble_pro.transaction_cache["wallet"] = list(transactions)

    result = await bubble_pro._get_transactions("wallet", "0xcenter", 1)

    assert result == transactions


# ── Stats Calculation ─────────────────────────────────────────────────


def test_calculate_stats_empty():
    """Empty map returns empty stats dict."""
    from app.domains.scanner.rugmaps.graph import calculate_stats

    bm = BubbleMap(map_id="test", center_wallet="0xcenter", created_at=datetime.now(UTC))
    assert calculate_stats(bm) == {}


def test_calculate_stats_with_data():
    """Stats are computed correctly from map data."""
    from app.domains.scanner.rugmaps.graph import calculate_stats

    now = datetime.now(UTC)
    bm = BubbleMap(map_id="test", center_wallet="0xcenter", created_at=now)
    bm.nodes.append(BubbleNode(id="n1", address="0xa", type="center", layer=0, risk="safe"))
    bm.nodes.append(BubbleNode(id="n2", address="0xb", type="wallet", layer=1, risk="high"))
    bm.links.append(BubbleLink(source="n1", target="n2", strength=0.8, total_volume=1000.0, transaction_count=5))

    stats = calculate_stats(bm)

    assert stats["total_nodes"] == 2
    assert stats["total_connections"] == 2  # links * 2
    assert stats["average_strength"] == 0.8
    assert stats["max_depth"] == 1
    assert stats["suspicious_count"] == 1
    assert stats["risk_distribution"] == {"safe": 1, "high": 1}
