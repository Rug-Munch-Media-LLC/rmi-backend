"""CLUSTER detection — wallet clustering beyond single-token BubbleMaps.

This package is the "cluster" engine for the structured ``RiskReport``. It goes
further than the market-standard single-token clustering (shared funder /
same-block buy / bundle groups — see ``holder_clusters.py``) by building EIGHT
graph structures that span LAUNCHES:

1. **Funder-tree** — the recursive funding tree for a token's top holders.
2. **Shared-deployer network** — deployers who launched multiple tokens whose
   top-holder sets overlap with this token's holders.
3. **Cross-token cluster graph** — wallet groups that co-appeared as top
   holders in >= 2 OTHER launches.
4. **Cross-token funder graph** — funders who seeded wallets across >= 2 OTHER
   launches.
5. **Temporal clusters (lockstep)** — wallets whose first buys land in the
   SAME block, or in a rapid run of consecutive launch blocks.
6. **Sniper / bot patterns** — launch-window signatures that a bot farm
   opened the book.
7. **MEV-bundle detection** — atomic multi-wallet buys in one block.
8. **Wash-trading clusters** — circular transfer cycles and self-swaps.

Every structure carries evidence and feeds a single 0-100 ``cluster_risk_score``
(higher = riskier) with a per-component breakdown.

Data sources (all LOCAL, zero cost — the same ``wallet_memory`` tables the
other own-data engines use). Best-effort by design: when ClickHouse is
unavailable every query degrades to an empty list and ``analyze_wallet_clusters``
returns a graceful ``unavailable`` payload.

Usage:
    from app.domains.scanner.engines.risk_cluster import analyze_wallet_clusters

    result = await analyze_wallet_clusters(
        "0x...", "ethereum",
        deployer="0x...",
        holders=["0xA", "0xB", ...],
        bundle={...},  # own_bundle_detect output (optional)
    )

NOTE (degodding 2026-09-03): this package is the public API + a re-export shim.
The implementation is split by concern:

  - ``_constants.py`` — query / analysis limits and address markers.
  - ``_builders.py`` — pure graph builders (funder tree, shared-deployer
    network, cross-token cluster graph, cross-token funder graph, temporal
    lockstep).
  - ``_launch_builders.py`` — sniper/bot patterns, MEV bundles, wash-trading
    clusters (pure, no database).
  - ``_scoring.py`` — ``score_cluster_risk`` composite scoring.
  - ``_queries.py`` — the ClickHouse query layer (``_query_*``). The client is
    resolved lazily through this package's ``_get_ch_client`` so tests that
    patch it keep working.
  - ``_resolve.py`` — input resolution + graceful ``unavailable`` result.
  - ``_orchestrator.py`` — ``analyze_wallet_clusters`` (the public API).
"""

from __future__ import annotations

from app.domains.scanner.engines._indexer_port import ch_client as _get_ch_client
from app.domains.scanner.engines.risk_cluster._builders import (
    build_funder_tree,
    detect_cross_token_clusters,
    detect_cross_token_funder_graphs,
    detect_shared_deployer_networks,
    detect_temporal_clusters,
)
from app.domains.scanner.engines.risk_cluster._launch_builders import (
    _describe_cycle,
    _empty_wash_result,
    detect_mev_bundles,
    detect_sniper_bot_patterns,
    detect_wash_trading_clusters,
)
from app.domains.scanner.engines.risk_cluster._orchestrator import analyze_wallet_clusters
from app.domains.scanner.engines.risk_cluster._queries import (
    _client,
    _query_cross_token_funders,
    _query_cross_token_overlap,
    _query_deploy_info,
    _query_deployer_token_overlap,
    _query_earliest_transfer,
    _query_first_buy_blocks,
    _query_first_funders,
    _query_launch_window_transfers,
    _query_token_transfers,
    _query_wallets_with_prior_tx,
)
from app.domains.scanner.engines.risk_cluster._resolve import (
    _query_deployer,
    _query_top_holders,
    _resolve_inputs,
    _resolve_launch_anchor,
    _unavailable_result,
)
from app.domains.scanner.engines.risk_cluster._scoring import score_cluster_risk

__all__ = [
    "analyze_wallet_clusters",
    "build_funder_tree",
    "detect_cross_token_clusters",
    "detect_cross_token_funder_graphs",
    "detect_mev_bundles",
    "detect_shared_deployer_networks",
    "detect_sniper_bot_patterns",
    "detect_temporal_clusters",
    "detect_wash_trading_clusters",
    "score_cluster_risk",
]
