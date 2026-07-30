"""
SENTINEL - Fund Flow Graph Builder
===================================
Builds a directed graph from wallet data for fund flow visualization.
"""

from __future__ import annotations

from typing import Any

from .models import FundFlowEdge, FundFlowNode


def _build_flow_graph(wallets: dict[str, Any], token_address: str) -> tuple:
    """Build node list and edge list from wallet data.

    Returns (nodes: list[FundFlowNode], edges: list[FundFlowEdge], risk_flags: list[str])
    """
    nodes: list[FundFlowNode] = []
    edges: list[FundFlowEdge] = []
    risk_flags: list[str] = []
    address_set = set()

    def _add_node(addr: str, role: str, amount_usd: float) -> str:
        """Add a node if not already present, return the address key."""
        key = addr.lower() if addr else ""
        if not key or key in address_set:
            return key
        address_set.add(key)
        nodes.append(FundFlowNode(address=addr, role=role, amount_usd=amount_usd))
        return key

    # 1. Creator node at top
    creator_addr = wallets.get("creator", "")
    if creator_addr:
        _add_node(creator_addr, "creator", 0)
    else:
        # Use a placeholder
        _add_node("UNKNOWN_CREATOR", "creator", 0)
        risk_flags.append("Creator address not found - fund origin untraceable")

    creator_key = (creator_addr or "UNKNOWN_CREATOR").lower()

    # 2. Funders (early buyers / LP providers)
    funders = wallets.get("funders", [])[:5]
    funder_keys = []
    for _i, f in enumerate(funders):
        addr = f.get("address", "")
        if not addr or addr.lower() == creator_key:
            continue
        amt = f.get("amount_usd", f.get("amount_sol", f.get("amount_raw", 0)))
        key = _add_node(addr, "funder", float(amt) if amt else 0)
        if key:
            funder_keys.append(key)
            edges.append(
                FundFlowEdge(
                    from_addr=creator_addr or "UNKNOWN_CREATOR",
                    to_addr=addr,
                    amount_usd=float(amt) if amt else 0,
                )
            )

    # 3. LP nodes (use first holder as LP proxy)
    holders = wallets.get("holders", [])[:3]
    lp_keys = []
    for _i, h in enumerate(holders):
        addr = h.get("address", "")
        if not addr or addr.lower() == creator_key or addr.lower() in address_set:
            continue
        bal = h.get("balance_usd", h.get("balance_raw", 0))
        key = _add_node(addr, "lp", float(bal) if bal else 0)
        if key:
            lp_keys.append(key)

    # Connect funders → LPs
    for fk in funder_keys:
        # Find the funder address from nodes
        funder_node = next((n for n in nodes if n.address.lower() == fk), None)
        if funder_node:
            for lk in lp_keys:
                lp_node = next((n for n in nodes if n.address.lower() == lk), None)
                if lp_node:
                    edges.append(
                        FundFlowEdge(
                            from_addr=funder_node.address,
                            to_addr=lp_node.address,
                            amount_usd=0,
                        )
                    )

    # 4. Sellers
    sellers = wallets.get("sellers", [])[:5]
    seller_keys = []
    for s in sellers:
        addr = s.get("address", "")
        if not addr or addr.lower() == creator_key or addr.lower() in address_set:
            continue
        amt = s.get("amount_usd", s.get("amount_sol", s.get("amount_raw", 0)))
        key = _add_node(addr, "seller", float(amt) if amt else 0)
        if key:
            seller_keys.append(key)

    # Connect LPs → Sellers (sellers sold from LP pool)
    for lk in lp_keys:
        lp_node = next((n for n in nodes if n.address.lower() == lk), None)
        if lp_node:
            for sk in seller_keys:
                seller_node = next((n for n in nodes if n.address.lower() == sk), None)
                if seller_node:
                    edges.append(
                        FundFlowEdge(
                            from_addr=lp_node.address,
                            to_addr=seller_node.address,
                            amount_usd=seller_node.amount_usd,
                        )
                    )

    # Fallback: if we have very few nodes, connect creator → some holders directly
    if len(nodes) < 3 and holders:
        for h in holders[:5]:
            addr = h.get("address", "")
            if addr and addr.lower() not in address_set:
                bal = h.get("balance_usd", h.get("balance_raw", 0))
                _add_node(addr, "other", float(bal) if bal else 0)
                edges.append(
                    FundFlowEdge(
                        from_addr=creator_addr or "UNKNOWN_CREATOR",
                        to_addr=addr,
                        amount_usd=float(bal) if bal else 0,
                    )
                )

    # Cap at 15 nodes
    if len(nodes) > 15:
        nodes = nodes[:15]
    if len(edges) > 20:
        edges = edges[:20]

    # Risk flag generation
    if len(nodes) <= 2:
        risk_flags.append("Very few wallets identified - possible low-activity or new token")
    creator_as_holder = any(n.address.lower() == creator_key and n.role != "creator" for n in nodes)
    if creator_as_holder:
        risk_flags.append("Creator still holds significant tokens - centralization risk")

    total_sellers = len([n for n in nodes if n.role == "seller"])
    total_funders = len([n for n in nodes if n.role == "funder"])
    if total_sellers > total_funders * 2:
        risk_flags.append("More sellers than funders - early dumping pattern")

    return nodes, edges, risk_flags
