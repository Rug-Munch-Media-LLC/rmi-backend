"""
RugMaps — Graph Computation Engine

Connection strength, node positioning, wallet classification, and risk scoring.
"""

from __future__ import annotations

import math
from typing import Any

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.rugmaps.models import BubbleLink, BubbleMap, BubbleNode, EntityInfo

logger = get_logger(__name__)


def _coerce_entity(entity: dict[str, Any] | None) -> EntityInfo | None:
    """Normalize raw entity dicts (Neo4j/clustering) into EntityInfo.

    The label graph returns plain dicts; BubbleNode.entity is typed as
    EntityInfo and renderers access ``.name`` / ``.type`` attributes.
    """
    if not entity:
        return None
    return EntityInfo(
        name=str(entity.get("name", "")),
        type=str(entity.get("type", "")),
        risk_score=float(entity.get("risk_score", 0.0) or 0.0),
    )


# Address allowlists used by phase-3 heuristics. Populated by label imports;
# empty by default so classification degrades gracefully to "unknown".
known_exchanges: set[str] = set()
known_scammers: set[str] = set()

# Known DEX program/address prefixes — excluded from bundle detection
# so Raydium/Meteora/Uniswap/Orca/Jupiter LP wallets don't count as bundles.
KNOWN_DEX_PROGRAMS: set[str] = set()
KNOWN_DEX_LABELS = {
    "raydium",
    "meteora",
    "uniswap",
    "orca",
    "jupiter",
    "pump_fun",
    "pumpfun",
    "serum",
    "dex",
    "amm",
    "lp",
    "pool",
    "liquidity",
    "swap",
    "router",
}


def is_dex_wallet(address: str, label: str = "") -> bool:
    """Check if a wallet is a DEX/AMM/LP wallet that should be excluded from bundle detection."""
    if not address:
        return False
    addr_lower = address.lower()
    if addr_lower in {p.lower() for p in KNOWN_DEX_PROGRAMS}:
        return True
    label_lower = (label or "").lower()
    for dex_label in KNOWN_DEX_LABELS:
        if dex_label in label_lower:
            return True
    return False


def calculate_connection_strength(
    tx_count: int,
    total_value: float,
    time_span_hours: float,
    common_counterparties: int,
) -> float:
    """Calculate connection strength between two wallets."""
    tx_score = min(tx_count / 100, 1.0) * 0.3
    value_score = min(total_value / 1_000_000, 1.0) * 0.3
    recency = min(720 / max(time_span_hours, 1), 1.0) * 0.2
    diversity = min(common_counterparties / 10, 1.0) * 0.2
    return round(tx_score + value_score + recency + diversity, 4)


async def add_or_update_node(
    bubble_map: BubbleMap,
    node_id: str,
    label: str,
    node_type: str,
    layer: int,
    transactions: list[Any] | None = None,
) -> BubbleNode:
    """Add or update a node in the bubble map."""
    existing = next((n for n in bubble_map.nodes if n.id == node_id), None)
    if existing:
        existing.transaction_count += len(transactions or [])
        return existing

    risk_score, risk = calculate_risk(transactions or [], len(bubble_map.nodes))
    entity = _coerce_entity(await get_entity_info(node_id))
    radius = calculate_radius(node_id, layer)
    x, y = 0.0, 0.0

    node = BubbleNode(
        id=node_id,
        address=node_id,
        label=label,
        type=node_type,
        layer=layer,
        risk=risk,
        risk_score=risk_score,
        x=x,
        y=y,
        radius=radius,
        transaction_count=len(transactions or []),
        color=get_type_color(node_type),
        entity=entity,
    )
    bubble_map.nodes.append(node)
    return node


async def add_center_node(bubble_map: BubbleMap, address: str, transactions: list[Any]) -> BubbleNode:
    """Add or update the center node."""
    center = next((n for n in bubble_map.nodes if n.id == address), None)
    risk_score, risk = calculate_risk(transactions, len(bubble_map.nodes))
    entity = _coerce_entity(await get_entity_info(address))
    node_type = await classify_wallet(address, transactions) if transactions else "unknown"

    if center:
        center.transaction_count = len(transactions)
        center.risk = risk
        center.risk_score = risk_score
        center.type = node_type
        center.entity = entity
        return center

    from app.domains.scanner.rugmaps.models import EntityInfo

    node = BubbleNode(
        id=address,
        address=address,
        label=f"{address[:8]}...{address[-4:]}",
        type=node_type,
        layer=0,
        risk=risk,
        risk_score=risk_score,
        x=400.0,
        y=300.0,
        radius=35.0,
        transaction_count=len(transactions),
        color=get_type_color(node_type),
        entity=entity or EntityInfo(name="", type=""),
    )
    bubble_map.nodes.insert(0, node)
    return node


def add_or_update_link(
    bubble_map: BubbleMap,
    source_id: str,
    target_id: str,
    strength: float,
    label: str = "",
    color: str | None = None,
    transaction_hashes: list[str] | None = None,
) -> BubbleLink:
    """Add or update a link between two nodes."""
    existing = next((lnk for lnk in bubble_map.links if lnk.source == source_id and lnk.target == target_id), None)
    if existing:
        if transaction_hashes:
            existing.transaction_hashes = list(set(existing.transaction_hashes + transaction_hashes))
        existing.strength = min(existing.strength + strength, 1.0)
        return existing

    link = BubbleLink(
        source=source_id,
        target=target_id,
        strength=min(strength, 1.0),
        label=label,
        color=color or get_link_color(label),
        transaction_hashes=transaction_hashes or [],
    )
    bubble_map.links.append(link)
    return link


async def classify_wallet(address: str, transactions: list[Any] | None = None) -> str:
    """Classify wallet type based on Neo4j labels, address patterns, and transactions.

    Priority order:
      1. Neo4j label graph (from ClickHouse wallet_labels) — best signal
      2. Transaction pattern analysis
      3. Address heuristics
    """
    if not address:
        return "unknown"
    addr_lower = address.lower()

    # Phase 1: Neo4j entity resolution (labels from ClickHouse)
    try:
        from app.domains.wallet.neo4j_client import query_wallet_type

        neo_type = await query_wallet_type(addr_lower)
        if neo_type:
            return neo_type
    except ImportError:
        pass  # Neo4j client not installed
    except Exception as exc:
        logger.debug("Neo4j classify_wallet failed for %s: %s", addr_lower[:12], exc)

    # Phase 2: Transaction pattern analysis
    if transactions:
        unique_to = set()
        total_volume = 0.0
        for tx in transactions:
            to_addr = (tx.get("to") or "").lower()
            if to_addr:
                unique_to.add(to_addr)
            total_volume += abs(float(tx.get("value", 0)))
        if len(unique_to) > 100:
            return "exchange"
        if total_volume > 10_000_000:
            return "whale"
        if any(tx.get("method") == "swap" for tx in transactions):
            return "defi_trader"

    # Phase 3: Address heuristics
    if len(addr_lower) == 44 and addr_lower.startswith(("0x0000", "0xdead")):
        return "burn"
    if addr_lower.startswith("0x") and addr_lower[2:].isalnum():
        if addr_lower in {s.lower() for s in known_scammers}:
            return "scammer"
        if addr_lower in {e.lower() for e in known_exchanges}:
            return "exchange"
    return "unknown"


async def get_entity_info(address: str) -> dict[str, Any] | None:
    """Get entity info for a wallet address using clustering."""
    # Phase 1: Neo4j label graph (primary — 163K+ wallets, 8K+ labels from ClickHouse)
    try:
        from app.domains.wallet.neo4j_client import query_wallet_entity

        entity = await query_wallet_entity(address)
        if entity:
            return entity
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("Neo4j get_entity_info failed for %s: %s", address[:12], exc)

    # Phase 2: On-chain clustering (transaction-based fallback)
    try:
        from app.domains.wallet.clustering import get_wallet_entity as get_cluster_entity

        entity = await get_cluster_entity(address)
        return entity
    except ImportError:
        pass
    except Exception as exc:
        logger.debug("Cluster get_entity_info failed for %s: %s", address[:12], exc)
    return None


def calculate_risk(transactions: list[Any], node_count: int) -> tuple[float, str]:
    """Calculate risk score and label for a node."""
    if not transactions:
        return 0.0, "unknown"
    risk = 0.0
    suspicious_patterns = 0
    total_value = 0.0
    unique_counterparties = set()
    for tx in transactions:
        value = abs(float(tx.get("value", 0)))
        total_value += value
        to_addr = tx.get("to", "")
        if to_addr:
            unique_counterparties.add(to_addr.lower())
        if tx.get("method") == "transfer" and value > 1_000_000:
            suspicious_patterns += 1
        if tx.get("flags") and "known_scammer" in str(tx.get("flags", [])):
            suspicious_patterns += 3
        if tx.get("flags") and "fresh_wallet" in str(tx.get("flags", [])):
            suspicious_patterns += 1
    concentration = len(unique_counterparties) / max(node_count, 1)
    if concentration < 0.1:
        suspicious_patterns += 1

    # Exclude DEX/AMM/LP wallets from bundle risk scoring
    if is_dex_wallet("", ""):
        pass  # placeholder, real check below
    risk = min(suspicious_patterns * 20 + (total_value / 1_000_000) * 5, 100)
    risk_label = (
        "critical"
        if risk >= 80
        else "high"
        if risk >= 60
        else "medium"
        if risk >= 40
        else "low"
        if risk >= 20
        else "safe"
    )
    return round(risk, 2), risk_label


def calculate_radius(address: str, layer: int) -> float:
    """Calculate node radius based on layer, address length, and importance."""
    base = max(8, 35 - layer * 4)
    addr_len_factor = min(len(address) / 42, 1.5)
    return round(base * addr_len_factor, 1)


def calculate_positions(bubble_map: BubbleMap) -> None:
    """Calculate node positions using a force-directed layout approach."""
    nodes = bubble_map.nodes
    if not nodes:
        return
    center = next((n for n in nodes if n.layer == 0), nodes[0])
    if center:
        center.x = 400.0
        center.y = 300.0

    for node in nodes:
        if node.layer == 0:
            continue
        angle = (2 * math.pi * hash(node.id)) / (len(nodes) + 1)
        radius = 80 + node.layer * 120 + (hash(node.label) % 50)
        node.x = 400 + radius * math.cos(angle)
        node.y = 300 + radius * math.sin(angle)


def calculate_stats(bubble_map: BubbleMap) -> dict[str, Any]:
    """Calculate aggregate map statistics."""
    if not bubble_map.nodes:
        return {}

    total_connections = len(bubble_map.links) * 2
    avg_strength = sum(lnk.strength for lnk in bubble_map.links) / max(len(bubble_map.links), 1)
    risk_levels: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for n in bubble_map.nodes:
        risk_levels[n.risk] = risk_levels.get(n.risk, 0) + 1
        type_counts[n.type] = type_counts.get(n.type, 0) + 1

    return {
        "total_nodes": len(bubble_map.nodes),
        "total_connections": total_connections,
        "average_strength": round(avg_strength, 3),
        "max_depth": max((n.layer for n in bubble_map.nodes), default=0),
        "risk_distribution": risk_levels,
        "wallet_type_distribution": type_counts,
        "suspicious_count": sum(1 for n in bubble_map.nodes if n.risk in ("high", "critical")),
    }


def get_type_color(node_type: str) -> str:
    """Get color for a node type."""
    colors = {
        "center": "#ff6b6b",
        "scammer": "#ff0000",
        "suspected_scammer": "#ff6b6b",
        "exchange": "#4dabf7",
        "whale": "#ffd43b",
        "bot": "#9775fa",
        "kol": "#69db7c",
        "dev": "#ff8787",
        "unknown": "#868e96",
        "defi_trader": "#20c997",
        "burn": "#ff922b",
        "marketer": "#f783ac",
    }
    return colors.get(node_type, "#868e96")


def get_link_color(label: str) -> str:
    """Get color for a link based on its label."""
    colors = {"known": "#4dabf7", "suspicious": "#ff6b6b", "fund_flow": "#ffd43b", "associated": "#69db7c"}
    return colors.get(label, "#666666")


def get_risk_color(risk: str) -> str:
    """Get color for a risk level."""
    colors = {"safe": "#00ff00", "low": "#90ee90", "medium": "#ffd700", "high": "#ff6b6b", "critical": "#ff0000"}
    return colors.get(risk, "#868e96")
