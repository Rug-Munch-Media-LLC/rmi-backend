"""Funding-edge construction.

A funding edge is a *value* claim (someone paid for the buy) and must name
the transaction that proves it. When the chain's value transfers are not
indexed, the fallback is a co-occurrence edge, which is explicitly weaker:
same first transaction, not same money. The two are never mixed in the
``kind`` field, so a consumer can weigh them differently.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines.known_entities import label_wallet

from .models import BundleEdge, WalletPosition

#: An exchange withdrawal inside this many seconds of the launch is treated
#: as launch preparation rather than aged capital.
CEX_WINDOW_SECONDS = 900

#: Minimum buyers a single funder must have paid before it becomes an edge.
MIN_FUNDED_BUYERS = 2


def _is_cex(address: str, chain: str) -> bool:
    return label_wallet(address, chain)["type"].startswith("cex_")


def _is_bridge(address: str, chain: str) -> bool:
    return label_wallet(address, chain)["type"].startswith("bridge")


def value_edges(
    transfers: list[dict[str, Any]],
    positions: dict[str, WalletPosition],
    chain: str,
    launch_ts: float,
) -> list[BundleEdge]:
    """Build funder -> buyer edges from native value transfers.

    Only transfers that precede the buyer's first purchase count. For each
    buyer the *latest* such transfer is kept: that is the money that could
    have paid for the buy, not an unrelated deposit from months earlier.
    """
    latest: dict[str, dict[str, Any]] = {}
    for row in transfers:
        buyer = str(row.get("to_address") or "")
        pos = positions.get(buyer)
        if pos is None or not row.get("from_address"):
            continue
        ts = float(row.get("ts") or 0)
        if ts and pos.first_ts and ts > pos.first_ts:
            continue
        current = latest.get(buyer)
        if current is None or float(row.get("ts") or 0) > float(current.get("ts") or 0):
            latest[buyer] = row

    edges: list[BundleEdge] = []
    for buyer, row in latest.items():
        funder = str(row.get("from_address"))
        ts = float(row.get("ts") or 0)
        if _is_bridge(funder, chain):
            kind = "bridge_in"
        elif _is_cex(funder, chain) and launch_ts and 0 <= launch_ts - ts <= CEX_WINDOW_SECONDS:
            kind = "cex_withdrawal"
        else:
            kind = "same_funder"
        edges.append(
            BundleEdge(
                from_address=funder,
                to_address=buyer,
                kind=kind,
                evidence_tx=str(row.get("signature") or ""),
                slot=int(row.get("slot") or 0),
                block_ts=ts,
                provenance="native_value_transfer",
            )
        )
    return edges


def shared_funder_groups(edges: list[BundleEdge]) -> dict[str, list[str]]:
    """Funders that paid at least ``MIN_FUNDED_BUYERS`` of the buyers."""
    groups: dict[str, list[str]] = {}
    for edge in edges:
        if edge.kind == "first_co_occurrence":
            continue
        groups.setdefault(edge.from_address, []).append(edge.to_address)
    return {f: sorted(set(b)) for f, b in groups.items() if len(set(b)) >= MIN_FUNDED_BUYERS}


def cooccurrence_edges(
    activity: list[dict[str, Any]],
    positions: dict[str, WalletPosition],
) -> list[BundleEdge]:
    """Weak edges: buyers whose earliest transaction names a shared account."""
    seen: dict[str, list[str]] = {}
    for row in activity:
        addr = str(row.get("address") or "")
        involved = row.get("involved") or []
        if addr not in positions:
            continue
        for other in involved:
            other = str(other)
            if other and other != addr:
                seen.setdefault(other, []).append(addr)

    edges: list[BundleEdge] = []
    for shared, buyers in seen.items():
        unique = sorted(set(buyers))
        if len(unique) < MIN_FUNDED_BUYERS:
            continue
        for buyer in unique:
            edges.append(
                BundleEdge(
                    from_address=shared,
                    to_address=buyer,
                    kind="first_co_occurrence",
                    evidence_tx="",
                    slot=positions[buyer].first_slot,
                    block_ts=positions[buyer].first_ts,
                    provenance="address_activity",
                    weight=0.4,
                )
            )
    return edges


def creator_funded_buyers(
    edges: list[BundleEdge],
    positions: dict[str, WalletPosition],
) -> list[str]:
    """Buyers paid by the token creator (a deployer-prep signal)."""
    creators = {addr for addr, pos in positions.items() if pos.is_creator}
    if not creators:
        return []
    return sorted(
        {
            edge.to_address
            for edge in edges
            if edge.from_address in creators and edge.to_address not in creators
        }
    )
