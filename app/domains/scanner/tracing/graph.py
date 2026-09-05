"""Cluster graph builder — wallet hops → RugMaps nodes/edges.

Produces the ``graph`` payload for POST /api/v1/tracing/decode in a shape
compatible with the RugMaps visualization engine: nodes carry an ``id``,
``address``, ``type`` (wallet | contract | token), and ``label``; edges carry
``source``/``target`` plus hop metadata (value, token, event).

Nodes are deduplicated by lowercase address. Any address that appears as a
``token`` on an edge is labeled a token/contract; everything else is a wallet.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.domains.scanner.tracing.models import GraphEdge, GraphNode, TraceGraph, WalletHop

logger = get_logger("graph")


def _short_label(address: str) -> str:
    """Compact label for the RugMaps canvas: 0x1234…abcd."""
    if len(address) <= 12:
        return address
    return f"{address[:8]}…{address[-4:]}"


def build_cluster_graph(
    hops: list[WalletHop],
    wallet_hops_extra: list[dict[str, Any]] | None = None,
) -> TraceGraph:
    """Build a cluster graph from wallet hops.

    Args:
        hops: WalletHop models produced by the hop mapper.
        wallet_hops_extra: optional raw hops (e.g. from debug_trace) that do
            not carry a token — included as additional edges.

    Returns a TraceGraph with deduplicated nodes and edges.
    """
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str, str], GraphEdge] = {}

    token_addresses: set[str] = set()

    def _add_edge(source: str, target: str, value: str, token: str, event: str) -> None:
        src = source.lower()
        dst = target.lower()
        if not src or not dst or src == dst:
            return
        token_l = token.lower() if token else ""
        key = (src, dst, token_l, event)
        if key in edges:
            # Accumulate value on duplicate hops
            try:
                old = int(edges[key].value or "0")
                new = int(value or "0")
                edges[key].value = str(old + new)
            except (ValueError, TypeError) as e:
                logger.error(f"_add_edge failed: {e}")
            return
        edges[key] = GraphEdge(source=src, target=dst, value=value, token=token_l, event=event)
        if token_l:
            token_addresses.add(token_l)

    for hop in hops:
        _add_edge(hop.from_, hop.to, hop.value, hop.token, hop.event)

    for raw_hop in wallet_hops_extra or []:
        _add_edge(
            str(raw_hop.get("from", "")),
            str(raw_hop.get("to", "")),
            str(raw_hop.get("value", "0")),
            str(raw_hop.get("token", "")),
            str(raw_hop.get("event", "internal_call")),
        )

    # Build nodes from all addresses seen on edges
    for edge in edges.values():
        for addr in (edge.source, edge.target):
            if addr in nodes:
                continue
            node_type = "token" if addr in token_addresses else "wallet"
            nodes[addr] = GraphNode(
                id=addr,
                address=addr,
                type=node_type,
                label=_short_label(addr),
            )
        if edge.token and edge.token not in nodes:
            nodes[edge.token] = GraphNode(
                id=edge.token,
                address=edge.token,
                type="token",
                label=_short_label(edge.token),
            )

    return TraceGraph(nodes=list(nodes.values()), edges=list(edges.values()))
