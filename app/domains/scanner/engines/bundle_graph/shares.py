"""Bundle share metrics for the public payload.

Each share answers a different question and has a different denominator, so
they are reported separately rather than merged into one "bundle %":

* supply share -- what the cluster still controls (needs proven supply),
* first-minute volume share -- how much of the opening flow it caused,
* early-token share -- what part of everything bought early it took.
"""

from __future__ import annotations

from typing import Any

from . import size
from .behaviour import first_minute_volume_usd
from .models import BundleGraph, WalletPosition


def volume_share(
    positions: dict[str, WalletPosition],
    graph: BundleGraph,
    rows: list[dict[str, Any]],
    token: str,
) -> float | None:
    """Top cluster notional as a share of first-minute notional."""
    if not graph.clusters:
        return None
    total = first_minute_volume_usd(rows, token, 60)
    if total <= 0:
        return None
    return size.pct(size.cluster_usd_spent(positions, graph.clusters[0].wallets), total)


def early_token_share(positions: dict[str, WalletPosition], graph: BundleGraph) -> float | None:
    """Top cluster still-held tokens as a share of all tokens bought early."""
    if not graph.clusters:
        return None
    total = sum(p.tokens_bought_raw for p in positions.values())
    return size.pct(size.cluster_net_tokens(positions, graph.clusters[0].wallets), total)
