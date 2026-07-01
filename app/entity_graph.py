"""
Entity Graph Visualization — Interactive Fund Flow for RugMaps + Scans
=======================================================================

Generates interactive entity relationship graphs showing wallet connections,
fund flows, and cluster relationships. SVG output for embedding in scan results
and RugMaps pages.

Premium feature: Visual forensics that sells in screenshots.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("entity.graph")


@dataclass
class GraphNode:
    id: str
    label: str
    node_type: str = "wallet"  # "wallet", "token", "contract", "entity", "cex"
    risk_score: float = 0.0
    chain: str = ""
    size: int = 20
    color: str = "#8B5CF6"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    source: str
    target: str
    edge_type: str = "transfer"  # "transfer", "deploy", "fund", "cluster", "counterparty"
    value_usd: float = 0.0
    tx_count: int = 1
    color: str = "#4B5563"
    width: float = 1.0


def build_entity_graph(
    center_address: str,
    chain: str = "ethereum",
    transactions: list[dict] | None = None,
    labels: dict[str, Any] | None = None,
    cluster_members: list[str] | None = None,
    depth: int = 2,
) -> dict[str, Any]:
    """Build an entity relationship graph from wallet data.

    Returns graph data suitable for visualization (SVG or Canvas).
    """
    nodes: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    # ── Center node ──
    center_id = f"{chain}:{center_address.lower()}"
    nodes[center_id] = GraphNode(
        id=center_id,
        label=_shorten_addr(center_address),
        node_type="wallet",
        chain=chain,
        size=30,
        color="#22D3EE",  # Cyan for center
        metadata={"address": center_address, "chain": chain},
    )

    # ── Process transactions ──
    txs = transactions or []
    counterparties = set()

    for tx in txs[:200]:  # Cap at 200 transactions
        if not isinstance(tx, dict):
            continue

        counterparty = tx.get("counterparty") or tx.get("to") or tx.get("from")
        if not counterparty or counterparty.lower() == center_address.lower():
            continue

        cp_id = f"{tx.get('chain', chain)}:{counterparty.lower()}"
        counterparties.add(cp_id)

        value = float(tx.get("value_usd", 0) or 0)
        tx_type = tx.get("type", "transfer")
        is_outgoing = tx.get("direction") == "out" or tx.get("from", "").lower() == center_address.lower()

        # Add counterparty node if new
        if cp_id not in nodes:
            # Determine node type and color
            node_type, color = _classify_node(counterparty, labels or {}, tx)
            nodes[cp_id] = GraphNode(
                id=cp_id,
                label=_shorten_addr(counterparty),
                node_type=node_type,
                chain=tx.get("chain", chain),
                size=18 if node_type == "wallet" else 22,
                color=color,
                metadata={"address": counterparty},
            )

        # Add edge
        edge_color = "#EF4444" if is_outgoing else "#22C55E"  # Red=out, Green=in
        edge_width = min(max(value / 10000, 1), 6) if value > 0 else 1

        edges.append(
            GraphEdge(
                source=center_id if is_outgoing else cp_id,
                target=cp_id if is_outgoing else center_id,
                edge_type=tx_type,
                value_usd=value,
                tx_count=1,
                color=edge_color,
                width=edge_width,
            )
        )

    # ── Add cluster members (if available) ──
    if cluster_members:
        cluster_color = "#F59E0B"  # Amber for cluster
        for member in cluster_members[:20]:
            if member.lower() == center_address.lower():
                continue
            member_id = f"{chain}:{member.lower()}"
            if member_id not in nodes:
                nodes[member_id] = GraphNode(
                    id=member_id,
                    label=_shorten_addr(member),
                    node_type="cluster_member",
                    color=cluster_color,
                    size=16,
                )
            edges.append(
                GraphEdge(
                    source=center_id,
                    target=member_id,
                    edge_type="cluster",
                    color=cluster_color,
                    width=1.5,
                )
            )

    # ── Add label-based nodes (CEX, scams, etc.) ──
    if labels:
        label_data = labels.get(center_address, labels)
        if isinstance(label_data, dict):
            label_type = label_data.get("label_type") or label_data.get("type", "")
            entity = label_data.get("entity") or label_data.get("name", "")

            if label_type in ("cex", "exchange"):
                nodes[center_id].node_type = "cex"
                nodes[center_id].color = "#F59E0B"  # Amber
                nodes[center_id].label = entity or "Exchange"
            elif label_type in ("scam", "phish", "hack"):
                nodes[center_id].node_type = "scam"
                nodes[center_id].color = "#EF4444"  # Red
                nodes[center_id].risk_score = 90
                nodes[center_id].label = entity or "Scam"
            elif label_type in ("mixer", "tumbler"):
                nodes[center_id].node_type = "mixer"
                nodes[center_id].color = "#8B5CF6"  # Purple
                nodes[center_id].risk_score = 70

    # ── Risk-based coloring ──
    for node in nodes.values():
        if node.risk_score >= 80:
            node.color = "#EF4444"  # Red
        elif node.risk_score >= 60:
            node.color = "#F97316"  # Orange
        elif node.risk_score >= 40:
            node.color = "#EAB308"  # Yellow

    # ── Convert to graph format ──
    return {
        "center": {"id": center_id, "address": center_address, "chain": chain},
        "nodes": [
            {
                "id": n.id,
                "label": n.label,
                "type": n.node_type,
                "chain": n.chain,
                "risk_score": n.risk_score,
                "size": n.size,
                "color": n.color,
                "metadata": n.metadata,
            }
            for n in nodes.values()
        ],
        "edges": [
            {
                "source": e.source,
                "target": e.target,
                "type": e.edge_type,
                "value_usd": e.value_usd,
                "tx_count": e.tx_count,
                "color": e.color,
                "width": e.width,
            }
            for e in edges
        ],
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "total_value_usd": sum(e.value_usd for e in edges),
            "entity_types": list({n.node_type for n in nodes.values()}),
            "risk_distribution": {
                "high": sum(1 for n in nodes.values() if n.risk_score >= 60),
                "medium": sum(1 for n in nodes.values() if 30 <= n.risk_score < 60),
                "low": sum(1 for n in nodes.values() if n.risk_score < 30),
            },
        },
    }


def generate_svg(graph_data: dict[str, Any], width: int = 800, height: int = 600) -> str:
    """Generate an SVG visualization of the entity graph.

    Uses a simple force-directed layout algorithm.
    """
    import math

    nodes = graph_data.get("nodes", [])
    edges = graph_data.get("edges", [])
    center = graph_data.get("center", {})
    center_id = center.get("id", "")

    if not nodes:
        return '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="200"><text x="400" y="100" text-anchor="middle" fill="#6B7280">No graph data</text></svg>'

    # Simple radial layout: center in middle, others in concentric circles
    cx, cy = width // 2, height // 2

    # Separate center from others
    other_nodes = [n for n in nodes if n["id"] != center_id]
    center_node = next((n for n in nodes if n["id"] == center_id), None)

    # Arrange other nodes in circles
    positions = {}
    if center_node:
        positions[center_id] = (cx, cy)

    rings = [
        (150, 0.0),  # radius, rotation offset
        (250, 0.3),
        (350, 0.15),
    ]

    node_idx = 0
    for ring_radius, rotation in rings:
        nodes_in_ring = other_nodes[node_idx : node_idx + max(8, len(other_nodes) // len(rings))]
        n = len(nodes_in_ring)
        for i, node in enumerate(nodes_in_ring):
            angle = (2 * math.pi * i / max(n, 1)) + rotation
            x = cx + ring_radius * math.cos(angle)
            y = cy + ring_radius * math.sin(angle)
            positions[node["id"]] = (x, y)
        node_idx += n
        if node_idx >= len(other_nodes):
            break

    # Build SVG
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" style="background:#07070b;font-family:monospace">',
        "<defs>",
        '<filter id="glow"><feGaussianBlur stdDeviation="3" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>',
        '<marker id="arrow-red" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="#EF4444"/></marker>',
        '<marker id="arrow-green" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="#22C55E"/></marker>',
        "</defs>",
        # Background grid
        '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1a2332" stroke-width="0.5"/></pattern>',
        f'<rect width="{width}" height="{height}" fill="url(#grid)"/>',
    ]

    # Draw edges
    for edge in edges:
        src_pos = positions.get(edge["source"])
        tgt_pos = positions.get(edge["target"])
        if not src_pos or not tgt_pos:
            continue

        x1, y1 = src_pos
        x2, y2 = tgt_pos

        # Curve edges slightly
        mid_x = (x1 + x2) / 2
        mid_y = (y1 + y2) / 2 - 30

        edge_color = edge.get("color", "#4B5563")
        edge_width = edge.get("width", 1)
        marker_attr = 'marker-end="url(#arrow-red)"' if edge["type"] == "transfer" else ""

        svg_parts.append(
            f'<path d="M{x1},{y1} Q{mid_x},{mid_y} {x2},{y2}" '
            f'stroke="{edge_color}" stroke-width="{edge_width}" fill="none" '
            f'opacity="0.4" {marker_attr}/>'
        )

    # Draw nodes
    for node in nodes:
        pos = positions.get(node["id"])
        if not pos:
            continue

        x, y = pos
        node_color = node.get("color", "#8B5CF6")
        node_size = node.get("size", 20)
        node_type = node.get("type", "wallet")

        # Different shapes by type
        if node_type == "scam" or node.get("risk_score", 0) >= 80:
            # Diamond for high-risk
            svg_parts.append(
                f'<polygon points="{x},{y - node_size} {x + node_size},{y} {x},{y + node_size} {x - node_size},{y}" '
                f'fill="{node_color}" fill-opacity="0.2" stroke="{node_color}" stroke-width="2" filter="url(#glow)"/>'
            )
        elif node_type == "cex" or node_type == "exchange":
            # Square for exchanges
            svg_parts.append(
                f'<rect x="{x - node_size}" y="{y - node_size}" width="{node_size * 2}" height="{node_size * 2}" '
                f'rx="4" fill="{node_color}" fill-opacity="0.2" stroke="{node_color}" stroke-width="2"/>'
            )
        else:
            # Circle for wallets
            svg_parts.append(
                f'<circle cx="{x}" cy="{y}" r="{node_size}" '
                f'fill="{node_color}" fill-opacity="0.2" stroke="{node_color}" stroke-width="2"/>'
            )

        # Label
        label = node.get("label", "")[:12]
        svg_parts.append(
            f'<text x="{x}" y="{y + node_size + 14}" text-anchor="middle" fill="#9CA3AF" font-size="10">{label}</text>'
        )

        # Center node highlight
        if node["id"] == center_id:
            svg_parts.append(
                f'<circle cx="{x}" cy="{y}" r="{node_size + 4}" '
                f'fill="none" stroke="#22D3EE" stroke-width="2" stroke-dasharray="4,2" opacity="0.5"/>'
            )

    # Legend
    legend_x = 20
    legend_y = height - 80
    legend_items = [
        ("#EF4444", "High Risk/Scam"),
        ("#F97316", "Medium Risk"),
        ("#22C55E", "Incoming"),
        ("#EF4444", "Outgoing"),
        ("#F59E0B", "Exchange/CEX"),
        ("#8B5CF6", "Wallet"),
    ]
    for i, (color, label) in enumerate(legend_items):
        lx = legend_x + (i % 3) * 130
        ly = legend_y + (i // 3) * 20
        svg_parts.append(f'<circle cx="{lx}" cy="{ly}" r="5" fill="{color}"/>')
        svg_parts.append(f'<text x="{lx + 12}" y="{ly + 4}" fill="#6B7280" font-size="10">{label}</text>')

    svg_parts.append("</svg>")
    return "\n".join(svg_parts)


def _classify_node(address: str, labels: dict[str, Any], tx: dict) -> tuple[str, str]:
    """Classify a counterparty node by type and color."""
    addr_lower = address.lower()

    # Check labels
    label_info = labels.get(address, labels.get(addr_lower, {}))
    if isinstance(label_info, dict):
        label_type = label_info.get("label_type", label_info.get("type", ""))
        if label_type in ("cex", "exchange"):
            return ("cex", "#F59E0B")
        if label_type in ("scam", "phish", "hack", "exploit"):
            return ("scam", "#EF4444")
        if label_type in ("mixer", "tumbler", "tornado"):
            return ("mixer", "#8B5CF6")
        if label_type in ("defi", "protocol"):
            return ("protocol", "#06B6D4")

    # Check transaction patterns
    tx_type = tx.get("type", "")
    if tx_type == "contract_creation" or tx_type == "deploy":
        return ("contract", "#10B981")
    if tx_type == "swap":
        return ("dex", "#06B6D4")

    return ("wallet", "#8B5CF6")


def _shorten_addr(addr: str) -> str:
    """Shorten address for display."""
    if not addr:
        return "?"
    if len(addr) <= 12:
        return addr
    return f"{addr[:6]}...{addr[-4:]}"
