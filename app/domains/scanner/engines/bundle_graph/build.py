"""Assemble the bundle graph for one token.

``build_bundle_graph`` is the only public entry point. It reads the launch
window, derives wallet positions, adds funding edges, groups the buyers, and
runs the multi-signal gate. Every step is optional: a chain with no indexed
value transfers still returns timing + size evidence, with the funding family
absent and the tier capped accordingly.

The function never raises. A dead data source returns a graph with
``confidence=0``, ``confidence_tier="none"`` and an explicit note, so the
caller can tell "nothing found" from "nothing read".
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines._logging import get_logger

from . import queries, timing
from .behaviour import build_positions
from .confidence import is_bundle, score_confidence
from .derive import build_clusters, fired_signals, sol_price
from .funding import cooccurrence_edges, shared_funder_groups, value_edges
from .models import BundleGraph
from .shares import early_token_share, volume_share

logger = get_logger(__name__)

DEFAULT_WINDOW_SLOTS = 5
DEFAULT_WINDOW_SECONDS = 120


async def build_bundle_graph(
    token: str,
    chain: str = "solana",
    *,
    window_slots: int = DEFAULT_WINDOW_SLOTS,
    window_seconds: int = DEFAULT_WINDOW_SECONDS,
) -> dict[str, Any]:
    """Build the bundle graph payload for ``token`` on ``chain``.

    Args:
        token: mint / contract address.
        chain: scanner chain name (``solana``, ``ethereum``, ``base`` ...).
        window_slots: launch window in slots/blocks.
        window_seconds: launch window in seconds.

    Returns:
        The :class:`BundleGraph` as a JSON-safe dict, always including
        ``wallets``, ``edges``, ``clusters``, ``confidence`` and the three
        bundle-share fields (``None`` when their denominator is unproven).
    """
    token = (token or "").strip()
    chain = (chain or "").strip().lower()
    graph = BundleGraph(
        token=token, chain=chain, window_slots=window_slots, window_seconds=window_seconds
    )

    try:
        window = _window(token, chain, graph, window_slots)
        if window is None:
            return graph.to_dict()
        rows, declared = window

        creator = str(declared.get("creator")) if declared and declared.get("creator") else None
        positions = build_positions(rows, token, creator=creator)
        graph.wallets = sorted(positions.values(), key=lambda p: p.first_slot)

        supply_raw, graph.supply_source = (
            queries.fetch_supply_raw(token, chain) if chain == "solana" else (None, "unavailable")
        )
        graph.executed_sol_price_usd = sol_price(rows)

        buyers = [p.address for p in graph.wallets][:200]
        launch_ts = min((p.first_ts for p in graph.wallets if p.first_ts), default=0.0)
        edges = value_edges(
            queries.fetch_value_transfers(buyers, chain, graph.launch_slot or 0),
            positions,
            chain,
            launch_ts,
        )
        if not edges:
            edges.extend(
                cooccurrence_edges(
                    queries.fetch_first_activity(buyers, chain, graph.launch_slot or 0), positions
                )
            )
            if edges:
                graph.notes.append(
                    "funding_edges_weak: no native value transfers indexed; co-occurrence only"
                )

        groups = timing.cluster_by_window(positions, window_slots)
        graph.edges = edges
        graph.clusters = build_clusters(
            positions, groups, supply_raw, graph.executed_sol_price_usd, edges
        )

        fired = fired_signals(positions, groups, edges, shared_funder_groups(edges))
        graph.signals = sorted(fired)
        graph.bundle_pct_of_supply = graph.clusters[0].supply_pct if graph.clusters else None
        graph.bundle_pct_of_first_minute_volume = volume_share(positions, graph, rows, token)
        graph.bundle_pct_of_early_tokens_bought = early_token_share(positions, graph)

        extra = int(min(15, (graph.bundle_pct_of_supply or 0) / 2))
        graph.confidence, graph.confidence_tier = score_confidence(fired, extra)
        if not is_bundle(graph.confidence_tier):
            graph.notes.append("single_family_evidence: not labelled a bundle")
        return graph.to_dict()
    except Exception as exc:
        logger.warning("bundle_graph_failed token=%s error=%s", token[:16], str(exc)[:200])
        graph.notes.append(f"bundle_graph_failed: {str(exc)[:120]}")
        return graph.to_dict()


def _window(
    token: str,
    chain: str,
    graph: BundleGraph,
    window_slots: int,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None] | None:
    """Resolve the launch slot and read the window's trade legs."""
    declared = queries.fetch_declared_launch(token, chain) if chain == "solana" else None
    launch_slot = int(declared["slot"]) if declared else queries.fetch_launch_slot(token, chain)
    if not launch_slot:
        graph.notes.append("no_trade_data: token has no indexed launch window")
        return None
    graph.launch_slot = launch_slot
    rows = queries.fetch_window_trades(token, chain, launch_slot, window_slots)
    if not rows:
        graph.notes.append("no_trade_data: launch window is empty")
        return None
    return rows, declared


__all__ = ["build_bundle_graph"]
