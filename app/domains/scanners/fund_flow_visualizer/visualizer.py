"""
SENTINEL - Fund Flow Visualizer
================================
SVG generation and the main FundFlowVisualizer class.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.chain_registry import is_evm, is_solana
from app.core.logging import get_logger
from app.domains.scanners._sentinel_registry import sentinel_module

from .fetchers import _fetch_evm_wallets, _fetch_solana_wallets
from .graph import _build_flow_graph
from .models import (
    SVG_OUTPUT_DIR,
    FundFlowEdge,
    FundFlowNode,
    FundFlowReport,
    _ensure_output_dir,
    shorten_address,
)

logger = get_logger("fund_flow_visualizer")

# Color scheme for node roles
ROLE_COLORS = {
    "creator": "#9b59b6",  # purple for creator
    "funder": "#2ecc71",  # green for funder
    "lp": "#f1c40f",  # yellow for LP
    "seller": "#e74c3c",  # red for seller
    "other": "#95a5a6",  # gray for other
}

ROLE_LABELS = {
    "creator": "Creator/Deployer",
    "funder": "Early Funder",
    "lp": "Liquidity Provider",
    "seller": "Early Seller",
    "other": "Other Holder",
}


def _generate_svg(
    nodes: list[FundFlowNode],
    edges: list[FundFlowEdge],
    token_address: str,
    chain: str,
) -> str:
    """Generate SVG fund flow graph with manual top-to-bottom layout."""

    if not nodes:
        # Return a minimal SVG indicating no data
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="200" '
            'viewBox="0 0 600 200">'
            '<rect width="600" height="200" fill="#1a1a2e"/>'
            '<text x="300" y="100" text-anchor="middle" fill="#e0e0e0" '
            'font-family="monospace" font-size="14">'
            f"No fund flow data for {shorten_address(token_address)} on {chain}"
            "</text></svg>"
        )

    # ── Layout parameters ────────────────────────────────────
    svg_width = 900
    node_height = 50
    node_width = 200
    y_spacing = 110  # vertical gap between tier rows
    margin_top = 80  # top margin for title
    margin_left = 50
    legend_width = 200
    graph_width = svg_width - legend_width - margin_left

    # ── Group nodes by role tier ──────────────────────────────
    tier_order = ["creator", "funder", "lp", "seller", "other"]
    tiers: dict[str, list[FundFlowNode]] = {t: [] for t in tier_order}
    for n in nodes:
        if n.role in tiers:
            tiers[n.role].append(n)
        else:
            tiers["other"].append(n)

    # Remove empty tiers
    active_tiers = [t for t in tier_order if tiers[t]]

    # ── Position nodes ───────────────────────────────────────
    node_positions: dict[str, tuple] = {}  # address_lower → (cx, cy)
    y = margin_top

    for tier in active_tiers:
        tier_nodes = tiers[tier]
        n_count = len(tier_nodes)
        x_start = margin_left
        x_span = graph_width - margin_left

        for i, n in enumerate(tier_nodes):
            if n_count == 1:
                cx = x_start + x_span // 2
            else:
                cx = x_start + int(i * x_span / (n_count - 1)) if n_count > 1 else x_start + x_span // 2
            cy = y + node_height // 2
            node_positions[n.address.lower()] = (cx, cy)

        y += node_height + y_spacing

    svg_height = y + 30  # bottom padding

    # ── Edge thickness proportional to flow ───────────────────
    max_amount = max((e.amount_usd for e in edges), default=1) or 1

    # ── Build SVG ─────────────────────────────────────────────
    lines: list[str] = []
    lines.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" '
        f'height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}">'
    )

    # Background
    lines.append(f'<rect width="{svg_width}" height="{svg_height}" fill="#1a1a2e" rx="8"/>')

    # Title
    short_addr = shorten_address(token_address)
    lines.append(
        f'<text x="{svg_width // 2}" y="30" text-anchor="middle" fill="#ffffff" '
        f'font-family="monospace" font-size="16" font-weight="bold">'
        f"Fund Flow: {short_addr} on {chain}</text>"
    )
    lines.append(
        f'<text x="{svg_width // 2}" y="50" text-anchor="middle" fill="#aaaacc" '
        f'font-family="monospace" font-size="11">'
        f"{len(nodes)} wallets · {len(edges)} flows</text>"
    )

    # ── Draw edges (lines with arrows) ────────────────────────
    lines.append("<defs>")
    lines.append(
        '<marker id="arrowhead" markerWidth="10" markerHeight="7" '
        'refX="10" refY="3.5" orient="auto">'
        '<polygon points="0 0, 10 3.5, 0 7" fill="#7f8fa6"/>'
        "</marker>"
    )
    # Color-coded arrow markers
    for role, color in ROLE_COLORS.items():
        lines.append(
            f'<marker id="arrow-{role}" markerWidth="10" markerHeight="7" '
            f'refX="10" refY="3.5" orient="auto">'
            f'<polygon points="0 0, 10 3.5, 0 7" fill="{color}"/>'
            f"</marker>"
        )
    lines.append("</defs>")

    for e in edges:
        from_key = e.from_addr.lower()
        to_key = e.to_addr.lower()
        from_pos = node_positions.get(from_key)
        to_pos = node_positions.get(to_key)
        if not from_pos or not to_pos:
            continue

        # Determine edge thickness (1-5 px)
        thickness = 1 + 4 * (e.amount_usd / max_amount) if max_amount > 0 and e.amount_usd > 0 else 2
        thickness = max(1, min(5, round(thickness)))

        # Determine color from source node role
        from_node = next((n for n in nodes if n.address.lower() == from_key), None)
        edge_color = ROLE_COLORS.get(from_node.role, "#7f8fa6") if from_node else "#7f8fa6"
        arrow_marker = f"arrow-{from_node.role}" if from_node and from_node.role in ROLE_COLORS else "arrowhead"

        # Draw line from bottom of source to top of target
        x1, y1 = from_pos
        x2, y2 = to_pos
        # Adjust to node edges
        y1_bottom = y1 + node_height // 2
        y2_top = y2 - node_height // 2

        # Use a curved path for non-vertical lines
        if abs(x1 - x2) > 20:
            mid_y = (y1_bottom + y2_top) / 2
            path_d = f"M {x1} {y1_bottom} C {x1} {mid_y}, {x2} {mid_y}, {x2} {y2_top}"
            lines.append(
                f'<path d="{path_d}" fill="none" stroke="{edge_color}" '
                f'stroke-width="{thickness}" stroke-opacity="0.7" '
                f'marker-end="url(#{arrow_marker})"/>'
            )
        else:
            lines.append(
                f'<line x1="{x1}" y1="{y1_bottom}" x2="{x2}" y2="{y2_top}" '
                f'stroke="{edge_color}" stroke-width="{thickness}" '
                f'stroke-opacity="0.7" marker-end="url(#{arrow_marker})"/>'
            )

        # Amount label at midpoint
        if e.amount_usd > 0:
            mx = (x1 + x2) / 2
            my = (y1_bottom + y2_top) / 2
            amt_str = f"${e.amount_usd:,.0f}" if e.amount_usd >= 1 else f"{e.amount_usd:.4f}"
            lines.append(
                f'<text x="{mx}" y="{my - 4}" text-anchor="middle" fill="#bdc3c7" '
                f'font-family="monospace" font-size="9">{amt_str}</text>'
            )

    # ── Draw nodes (rounded rectangles) ──────────────────────
    for n in nodes:
        pos = node_positions.get(n.address.lower())
        if not pos:
            continue
        cx, cy = pos
        color = ROLE_COLORS.get(n.role, "#95a5a6")
        rx = cx - node_width // 2
        ry = cy - node_height // 2

        # Rect
        lines.append(
            f'<rect x="{rx}" y="{ry}" width="{node_width}" height="{node_height}" '
            f'rx="8" fill="#2d2d44" stroke="{color}" stroke-width="2"/>'
        )

        # Address text
        label = shorten_address(n.address)
        lines.append(
            f'<text x="{cx}" y="{cy - 2}" text-anchor="middle" fill="#ffffff" '
            f'font-family="monospace" font-size="12" font-weight="bold">{label}</text>'
        )

        # Role tag
        role_label = n.role.upper()
        lines.append(
            f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" fill="{color}" '
            f'font-family="monospace" font-size="9">{role_label}</text>'
        )

    # ── Legend ────────────────────────────────────────────────
    legend_x = svg_width - legend_width
    legend_y = margin_top
    lines.append(
        f'<rect x="{legend_x}" y="{legend_y}" width="{legend_width - 10}" '
        f'height="{len(ROLE_COLORS) * 24 + 30}" rx="6" fill="#2d2d44" '
        f'stroke="#444466" stroke-width="1"/>'
    )
    lines.append(
        f'<text x="{legend_x + 10}" y="{legend_y + 18}" fill="#ffffff" '
        f'font-family="monospace" font-size="10" font-weight="bold">LEGEND</text>'
    )
    for i, (role, color) in enumerate(ROLE_COLORS.items()):
        ly = legend_y + 30 + i * 24
        lines.append(f'<rect x="{legend_x + 10}" y="{ly}" width="14" height="14" rx="3" fill="{color}"/>')
        lines.append(
            f'<text x="{legend_x + 30}" y="{ly + 12}" fill="#e0e0e0" '
            f'font-family="monospace" font-size="10">{ROLE_LABELS[role]}</text>'
        )

    lines.append("</svg>")
    return "\n".join(lines)


@sentinel_module(key="fund_flow", tier="premium", description="Fund-flow visualization from deployer wallet")
class FundFlowVisualizer:
    """Generate SVG fund flow visualization for a token."""

    async def analyze(self, token_address: str, chain: str) -> FundFlowReport:
        """Analyze token fund flow and generate SVG visualization.

        Args:
            token_address: Token contract/mint address.
            chain: Blockchain (solana, ethereum, base, bsc, etc.).

        Returns:
            FundFlowReport with SVG content, nodes, edges, and risk assessment.
        """
        logger.info(f"FundFlowVisualizer: analyzing {token_address} on {chain}")

        # ── Fetch wallet data ──────────────────────────────────
        wallets: dict[str, Any] = {
            "creator": None,
            "funders": [],
            "sellers": [],
            "holders": [],
        }

        try:
            if is_solana(chain):
                wallets = await _fetch_solana_wallets(token_address)
            elif is_evm(chain):
                wallets = await _fetch_evm_wallets(token_address, chain)
            else:
                logger.warning(f"Unsupported chain '{chain}' for fund flow, attempting generic fetch")
                wallets = await _fetch_evm_wallets(token_address, chain)
        except httpx.HTTPError as e:
            logger.warning(f"Wallet data fetch failed: {e}")

        # ── Build flow graph ───────────────────────────────────
        nodes, edges, risk_flags = _build_flow_graph(wallets, token_address)

        # ── Compute risk score ────────────────────────────────
        risk_score = 0
        if not wallets.get("creator"):
            risk_score += 25
        if len(nodes) <= 2:
            risk_score += 20
        sellers_count = len([n for n in nodes if n.role == "seller"])
        funders_count = len([n for n in nodes if n.role == "funder"])
        if sellers_count > funders_count and funders_count > 0:
            risk_score += 15
        if sellers_count > 3:
            risk_score += 10
        if not wallets.get("holders"):
            risk_score += 10
        risk_score = min(100, risk_score)

        if risk_score < 25:
            risk_level = "low"
        elif risk_score < 50:
            risk_level = "moderate"
        elif risk_score < 75:
            risk_level = "high"
        else:
            risk_level = "critical"

        # ── Generate SVG ───────────────────────────────────────
        svg_content = _generate_svg(nodes, edges, token_address, chain)

        # ── Save SVG to file ───────────────────────────────────
        _ensure_output_dir()
        safe_name = token_address.replace("/", "_").replace("\\", "_")
        svg_path = os.path.join(SVG_OUTPUT_DIR, f"{safe_name}.svg")
        try:
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg_content)
            logger.info(f"Fund flow SVG saved to {svg_path}")
        except OSError as e:
            logger.warning(f"Failed to save SVG to {svg_path}: {e}")
            svg_path = ""

        # ── Build report ───────────────────────────────────────
        node_dicts = [{"address": n.address, "role": n.role, "amount_usd": n.amount_usd} for n in nodes]
        edge_dicts = [{"from": e.from_addr, "to": e.to_addr, "amount_usd": e.amount_usd} for e in edges]

        return FundFlowReport(
            svg_content=svg_content,
            svg_path=svg_path,
            nodes=node_dicts,
            edges=edge_dicts,
            risk_flags=risk_flags,
            risk_score=risk_score,
            risk_level=risk_level,
        )
