"""Derivation helpers: signals, clusters, and the executed SOL price.

Split out of :mod:`build` so the orchestrator stays a readable sequence of
steps and each piece can be unit-tested without a data source.
"""

from __future__ import annotations

from typing import Any

from . import size, timing
from .funding import creator_funded_buyers
from .models import BundleCluster, WalletPosition

SOL_MINT = "So11111111111111111111111111111111111111112"


def sol_price(rows: list[dict[str, Any]]) -> float | None:
    """Executed SOL price from the window's SOL-quoted legs, when present."""
    for row in rows:
        if SOL_MINT in (row.get("token0"), row.get("token1")):
            price = float(row.get("price_usd") or 0)
            if price > 0:
                return round(price, 4)
    return None


def fired_signals(
    positions: dict[str, WalletPosition],
    clusters: list[list[str]],
    edges: list[Any],
    funder_groups: dict[str, list[str]],
) -> set[str]:
    """The signal set supported by what was actually observed."""
    fired: set[str] = set()
    if timing.same_slot_groups(positions):
        fired.add("same_slot_buys")
    if clusters:
        fired.add("early_slot_cluster")
    if funder_groups:
        fired.add("shared_funder")
    kinds = {edge.kind for edge in edges}
    if "cex_withdrawal" in kinds:
        fired.add("cex_within_window")
    if "bridge_in" in kinds:
        fired.add("bridge_in")
    if any(size.similar_size(positions, group) for group in clusters):
        fired.add("similar_size")
    if creator_funded_buyers(edges, positions):
        fired.add("creator_funded")
    return fired


def build_clusters(
    positions: dict[str, WalletPosition],
    groups: list[list[str]],
    supply_raw: int | None,
    sol_price_usd: float | None,
    edges: list[Any],
) -> list[BundleCluster]:
    """Turn wallet groups into clusters carrying their own economics."""
    edge_map: dict[str, set[str]] = {}
    for edge in edges:
        edge_map.setdefault(edge.to_address, set()).add(edge.kind)

    out: list[BundleCluster] = []
    for wallets in groups:
        kinds: list[str] = []
        for wallet in wallets:
            kinds.extend(edge_map.get(wallet, set()))
        realised = size.cluster_realised_usd(positions, wallets)
        out.append(
            BundleCluster(
                wallets=wallets,
                first_slot=min(positions[w].first_slot for w in wallets),
                span_slots=timing.span_slots(positions, wallets),
                supply_pct=size.supply_pct(size.cluster_net_tokens(positions, wallets), supply_raw),
                realised_usd=realised,
                realised_sol=size.realised_sol(realised, sol_price_usd),
                still_holding=timing.cluster_still_holding(positions, wallets),
                evidence_kinds=kinds,
            )
        )
    out.sort(key=lambda c: (c.supply_pct or 0, c.size), reverse=True)
    return out
