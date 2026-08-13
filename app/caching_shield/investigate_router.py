"""
RMI Investigative Framework - FastAPI Router v2
Solana + EVM funding tracing, risk scanning, token analysis.
All backed by the unified data fallback engine.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/investigate", tags=["investigate"])


class TraceRequest(BaseModel):
    address: str
    chain_id: int | None = 1
    chain: str | None = "ethereum"
    max_depth: int = 2


class ScanRequest(BaseModel):
    address: str
    chain_id: int | None = None
    chain: str | None = "solana"


class GraphRequest(BaseModel):
    address: str
    chain: str | None = "solana"
    depth: int = 3


CHAIN_NAMES = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "base": 8453,
    "arbitrum": 42161,
    "optimism": 10,
    "avalanche": 43114,
    "fantom": 250,
    "gnosis": 100,
    "solana": 0,
}


@router.get("/chains")
async def list_chains() -> dict[str, Any]:
    return {
        "chains": [
            {"name": "Solana", "id": 0},
            {"name": "Ethereum", "id": 1},
            {"name": "BSC", "id": 56},
            {"name": "Polygon", "id": 137},
            {"name": "Base", "id": 8453},
            {"name": "Arbitrum", "id": 42161},
            {"name": "Optimism", "id": 10},
            {"name": "Avalanche", "id": 43114},
            {"name": "Fantom", "id": 250},
            {"name": "Gnosis", "id": 100},
        ],
        "default": "solana",
    }


@router.post("/trace")
async def trace_wallet_funding(req: TraceRequest) -> Any:
    """Trace funding source for any wallet (Solana or EVM)."""
    chain = (req.chain or "").lower()
    chain_id = req.chain_id or CHAIN_NAMES.get(chain, 1)

    if chain == "solana":
        return await _trace_solana(req.address)
    else:
        return await _trace_evm(req.address, chain_id, req.max_depth)


@router.post("/scan")
async def full_investigation(req: ScanRequest) -> dict[str, Any]:
    """Full investigation: trace + risk + labels."""
    chain = (req.chain or "").lower()
    chain_id = req.chain_id or CHAIN_NAMES.get(chain, 1)

    from app.caching_shield.data_fallback import DataQueryType, get_data_engine

    engine = get_data_engine()

    # Risk scan (works for both Solana and EVM)
    risk = await engine.query(DataQueryType.RISK_SCAN, address=req.address, chain=chain)

    # Labels
    labels = None
    try:
        from app.wallet_label_loader import lookup_wallet_label

        label = lookup_wallet_label(req.address)
        labels = [{"address": req.address, "label": label}] if label else []
    except (AttributeError, ImportError):
        labels = []

    # Trace
    if chain == "solana":
        trace = await _trace_solana(req.address)
    else:
        trace = await _trace_evm(req.address, chain_id, 3)

    return {
        "wallet": req.address,
        "chain": chain,
        "trace": trace,
        "risk": risk,
        "labels": labels,
        "queried_at": time.time(),
    }


@router.get("/health")
async def fallback_health() -> Any:
    """Check fallback engine status."""
    from app.caching_shield.data_fallback import get_data_engine

    return get_data_engine().health()


@router.post("/graph")
async def investigation_graph(req: GraphRequest) -> dict[str, Any | None]:
    """
    Return knowledge graph subgraph as cytoscape.js-compatible JSON.
    Centers on the given address, explores outward up to `depth` hops.
    Returns nodes + edges with type-specific colors and metadata.
    """
    from app.knowledge_graph import NodeType, get_knowledge_graph

    kg = await get_knowledge_graph()

    # Try multiple node types to find the address
    node_type = NodeType.WALLET
    node_id = req.address
    node = None

    for nt in [NodeType.WALLET, NodeType.TOKEN, NodeType.ADDRESS, NodeType.CONTRACT]:
        node = await kg.get_node(nt, node_id)
        if node:
            node_type = nt
            break

    if not node:
        # Try with address casing variations
        for nt in [NodeType.WALLET, NodeType.TOKEN, NodeType.ADDRESS]:
            node = await kg.get_node(nt, node_id.lower())
            if node:
                node_type = nt
                node_id = node_id.lower()
                break

    # If node still not found, create a placeholder
    if not node:
        node_type = NodeType.ADDRESS
        node = {"label": node_id[:12] + "...", "type": NodeType.ADDRESS, "metadata": {}}

    # Get subgraph
    raw = await kg.get_subgraph(
        node_type=node_type,
        node_id=node_id,
        depth=min(req.depth, 5),
        max_nodes=80,
    )

    # ── Convert to cytoscape.js JSON ──────────────────────────
    node_colors = {
        "wallet": "#3498db",
        "token": "#2ecc71",
        "contract": "#e67e22",
        "scam": "#e74c3c",
        "entity": "#9b59b6",
        "chain": "#1abc9c",
        "address": "#95a5a6",
    }
    node_shapes = {
        "wallet": "ellipse",
        "token": "round-rectangle",
        "contract": "rectangle",
        "scam": "triangle",
        "entity": "diamond",
        "chain": "hexagon",
        "address": "ellipse",
    }

    cy_nodes = []
    cy_edges = []

    for key, nd in raw.get("nodes", {}).items():
        ntype = nd.get("type", "address")
        cy_nodes.append(
            {
                "data": {
                    "id": key,
                    "label": nd.get("label", nd.get("id", "")),
                    "type": ntype,
                    "color": node_colors.get(ntype, "#95a5a6"),
                    "shape": node_shapes.get(ntype, "ellipse"),
                    "size": 45 if key == raw.get("center") else 30,
                    "metadata": nd.get("metadata", {}),
                },
                "classes": f"node-{ntype}" + (" center" if key == raw.get("center") else ""),
            }
        )

    for edge in raw.get("edges", []):
        rel = edge.get("relation", "associated")
        cy_edges.append(
            {
                "data": {
                    "id": f"{edge['from']}→{edge['to']}",
                    "source": edge["from"],
                    "target": edge["to"],
                    "label": rel,
                    "weight": edge.get("weight", 0.5),
                    "relation": rel,
                },
                "classes": f"edge-{rel}",
            }
        )

    return {
        "center": raw.get("center", ""),
        "nodes": cy_nodes,
        "edges": cy_edges,
        "depth": raw.get("depth", 0),
        "total_nodes": len(cy_nodes),
        "total_edges": len(cy_edges),
        "chain": req.chain,
        "queried_at": time.time(),
    }


@router.post("/fund-flow")
async def fund_flow_sankey(req: GraphRequest) -> dict[str, Any]:
    """
    Return fund flow data formatted for D3 Sankey diagram.
    Shows creator → funders → LPs → sellers flow with amounts.
    Falls back to cytoscape-compatible format if Sankey data is sparse.
    """
    chain = (req.chain or "").lower()
    address = req.address.strip()

    # Get fund flow data using existing visualizer
    from app.domains.scanners.fund_flow_visualizer import (
        _build_flow_graph,
        _fetch_evm_wallets,
        _fetch_solana_wallets,
    )

    if chain == "solana" or (not address.startswith("0x")):
        wallets = await _fetch_solana_wallets(address)
    else:
        wallets = await _fetch_evm_wallets(address, chain)

    nodes, edges, risk_flags = _build_flow_graph(wallets, address)

    # ── Convert to D3 Sankey format ──────────────────────────
    sankey_nodes = []
    sankey_links = []
    node_index: dict[str, Any] = {}

    for n in nodes:
        idx = len(sankey_nodes)
        addr_key = n.address.lower()
        node_index[addr_key] = idx
        sankey_nodes.append(
            {
                "id": idx,
                "name": n.address[:10] + "..." + n.address[-6:] if len(n.address) > 16 else n.address,
                "address": n.address,
                "role": n.role,
                "amount_usd": n.amount_usd,
            }
        )

    for e in edges:
        from_key = e.from_addr.lower()
        to_key = e.to_addr.lower()
        if from_key in node_index and to_key in node_index:
            sankey_links.append(
                {
                    "source": node_index[from_key],
                    "target": node_index[to_key],
                    "value": max(e.amount_usd, 1.0),
                }
            )

    # Also generate cytoscape nodes/edges for graph overlay
    cy_nodes = []
    cy_edges = []
    role_colors = {
        "creator": "#9b59b6",
        "funder": "#2ecc71",
        "lp": "#f1c40f",
        "seller": "#e74c3c",
        "other": "#95a5a6",
    }
    role_labels = {
        "creator": "Creator/Deployer",
        "funder": "Early Funder",
        "lp": "LP Holder",
        "seller": "Early Seller",
        "other": "Holder",
    }

    for n in nodes:
        short = n.address[:8] + "..." + n.address[-6:] if len(n.address) > 16 else n.address
        cy_nodes.append(
            {
                "data": {
                    "id": n.address,
                    "label": f"{role_labels.get(n.role, n.role)}\n{short}",
                    "type": n.role,
                    "color": role_colors.get(n.role, "#95a5a6"),
                    "size": 40 if n.role == "creator" else 28,
                    "amount_usd": n.amount_usd,
                },
                "classes": f"flow-{n.role}",
            }
        )

    for e in edges:
        cy_edges.append(
            {
                "data": {
                    "id": f"flow-{e.from_addr[:8]}→{e.to_addr[:8]}",
                    "source": e.from_addr,
                    "target": e.to_addr,
                    "label": f"${e.amount_usd:,.0f}",
                    "weight": max(min(e.amount_usd / 1000, 1.0), 0.1),
                    "relation": "fund_flow",
                },
                "classes": "flow-edge",
            }
        )

    return {
        "address": address,
        "chain": chain,
        "sankey": {"nodes": sankey_nodes, "links": sankey_links},
        "graph": {"nodes": cy_nodes, "edges": cy_edges},
        "risk_flags": risk_flags,
        "risk_score": len(risk_flags) * 15 + (30 if len(nodes) < 3 else 0),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "queried_at": time.time(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# TRACING HELPERS
# ═══════════════════════════════════════════════════════════════════════════


async def _trace_solana(address: str) -> dict[str, Any]:
    from app.caching_shield.data_fallback import DataQueryType, get_data_engine

    engine = get_data_engine()
    result = await engine.query(DataQueryType.SOLANA_FUNDING, address=address)
    if result:
        return {
            "source": result.get("source", "unknown"),
            "first_tx": result.get("first_tx", ""),
            "balance_change_sol": result.get("balance_change", 0),
            "total_value_usd": result.get("total_value", 0),
            "signatures_found": result.get("signatures_found", 0),
            "label": result.get("label", ""),
            "is_cex": result.get("is_cex", False),
        }
    return {"source": "none", "error": "no data from any provider"}


async def _trace_evm(address: str, chain_id: int, max_depth: int) -> dict[str, Any]:
    from app.caching_shield.funding_tracer import trace_funding_source

    result = await trace_funding_source(address, chain_id, max_depth)
    return {
        "source_type": result.source_type,
        "source_address": result.source_address,
        "source_label": result.source_label,
        "confidence": result.confidence,
        "funding_tx": result.funding_tx_hash,
        "funding_amount_eth": result.funding_amount_eth,
        "hops": result.hops,
        "errors": result.errors,
    }
