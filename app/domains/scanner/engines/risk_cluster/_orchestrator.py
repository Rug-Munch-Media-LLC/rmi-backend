"""Public orchestrator for the risk_cluster package.

``analyze_wallet_clusters`` runs the eight beyond-market cluster structures
(funder tree, shared-deployer network, cross-token cluster graph, cross-token
funder graph, temporal lockstep, sniper/bot patterns, MEV bundles, wash-trading
clusters) and aggregates them into a single 0-100 cluster risk score.
"""

from __future__ import annotations

import time
from typing import Any

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.domains.scanner.engines._common import run_sync
from app.domains.scanner.engines._indexer_port import normalize_chain as _normalize_chain
from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.engines.risk_cluster._builders import (
    build_funder_tree,
    detect_cross_token_clusters,
    detect_cross_token_funder_graphs,
    detect_shared_deployer_networks,
    detect_temporal_clusters,
)
from app.domains.scanner.engines.risk_cluster._constants import (
    _DEAD_ADDR,
    _LAUNCH_WINDOW_BLOCKS,
    _MAX_FUNDER_ROWS,
    _MAX_HOLDERS,
    _MAX_LAUNCH_ROWS,
    _MAX_OVERLAP_TOKENS,
    _MAX_WASH_TRANSFER_ROWS,
    _WASH_LOOKBACK_HOURS,
    _ZERO_ADDR,
)
from app.domains.scanner.engines.risk_cluster._launch_builders import (
    detect_mev_bundles,
    detect_sniper_bot_patterns,
    detect_wash_trading_clusters,
)
from app.domains.scanner.engines.risk_cluster._queries import (
    _query_cross_token_funders,
    _query_cross_token_overlap,
    _query_deployer_token_overlap,
    _query_first_buy_blocks,
    _query_first_funders,
    _query_launch_window_transfers,
    _query_token_transfers,
    _query_wallets_with_prior_tx,
)
from app.domains.scanner.engines.risk_cluster._resolve import (
    _resolve_inputs,
    _resolve_launch_anchor,
    _unavailable_result,
)
from app.domains.scanner.engines.risk_cluster._scoring import score_cluster_risk

logger = get_logger(__name__)


async def analyze_wallet_clusters(
    token_address: str,
    chain: str,
    *,
    deployer: str | None = None,
    holders: list[str] | None = None,
    bundle: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute the eight beyond-market cluster structures for a token.

    Args:
        token_address: Token contract address.
        chain: Chain name (ethereum, bsc, solana, ...).
        deployer: Deployer address (optional — enables the shared-deployer
            network leg). Resolved from own data when omitted.
        holders: Top-N holder addresses (optional — enables the cross-token
            overlap leg). Resolved from own data when omitted.
        bundle: ``own_bundle_detect`` output (optional) — used to weigh the
            cluster risk score when a launch bundle is already confirmed.

    Returns:
        A dict with ``result`` in (``"scanned"`` | ``"unavailable"``) plus::

            {
                "funder_tree": [{"funder", "funder_is_deployer",
                                 "holders": [...], "holder_count", "share_pct",
                                 "evidence": [tx_hashes]}],
                "shared_deployer_network": [{"deployer", "tokens": [...],
                                             "shared_wallets": [...], "overlap"}],
                "cross_token_clusters": [{"token", "wallets": [...],
                                          "members", "deployer", "overlap"}],
                "cross_token_funders": [{"funder", "tokens": [...],
                                         "launch_count", "funded_wallets": [...]}],
                "temporal_clusters": [{"block", "wallets": [...], "members",
                                       "span_blocks", "early_launch"}],
                "sniper_patterns": [{"pattern", "wallets": [...], "members",
                                     "detail"}],
                "mev_bundles": [{"pattern", "block", "wallets": [...],
                                 "members", "detail"}],
                "wash_trading": {"cycles": [...], "self_swaps": [...],
                                 "cycle_volume_ratio", "wash_score"},
                "cluster_risk_score": 0-100,
                "cluster_risk_label": "LOW" | "MEDIUM" | "HIGH" | "CRITICAL",
                "cluster_risk_description": "...",
                "component_scores": {structure -> points},
                "deployer": "...", "holders_analyzed": N, "source": "own",
            }

        Returns an ``unavailable`` payload (graceful degradation) when
        ClickHouse is unreachable or no cluster data exists.
    """
    token_address = (token_address or "").lower().strip()
    chain_norm = _normalize_chain(chain)
    _start = time.monotonic()

    if not token_address or not chain_norm:
        return _unavailable_result(token_address, chain_norm, "token_address and chain are required")

    # ── Resolve inputs from own data when not provided ──
    try:
        resolved = await _resolve_inputs(token_address, chain_norm, deployer=deployer, holders=holders)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError):
        resolved = {"deployer": deployer, "holders": holders or []}

    deployer = resolved.get("deployer")
    holder_addrs = resolved.get("holders") or []
    holder_addrs = [h.lower().strip() for h in holder_addrs if h and h.lower().strip() not in (_ZERO_ADDR, _DEAD_ADDR)]
    holder_addrs = list(dict.fromkeys(holder_addrs))[:_MAX_HOLDERS]

    # ── Leg 1: funder tree (first funder per holder, own transfers) ──
    first_in: list[dict[str, Any]] = []
    try:
        first_in = await run_sync(_query_first_funders, token_address, chain_norm) or []
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("risk_cluster_first_funders_failed: %s", exc)
        first_in = []

    funder_tree = build_funder_tree(first_in, deployer=deployer)

    # ── Leg 2: shared-deployer network (other launches, same deployer) ──
    shared_deployers: list[dict[str, Any]] = []
    if deployer and holder_addrs:
        try:
            overlap_rows = await run_sync(
                _query_deployer_token_overlap,
                deployer,
                holder_addrs,
                token_address,
                chain_norm,
                _MAX_OVERLAP_TOKENS,
            )
            shared_deployers = detect_shared_deployer_networks(overlap_rows or [], deployer)
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            CHDriverError,
        ) as exc:
            logger.debug("risk_cluster_shared_deployer_failed: %s", exc)
            shared_deployers = []

    # ── Leg 3: cross-token cluster graph (holders co-appear in other launches) ──
    cross_token: list[dict[str, Any]] = []
    if holder_addrs:
        try:
            overlap_rows = await run_sync(
                _query_cross_token_overlap,
                holder_addrs,
                token_address,
                chain_norm,
                _MAX_OVERLAP_TOKENS,
            )
            cross_token = detect_cross_token_clusters(overlap_rows or [])
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            CHDriverError,
        ) as exc:
            logger.debug("risk_cluster_cross_token_failed: %s", exc)
            cross_token = []

    # ── Leg 4: cross-token funder graph (funders seeding >= 2 other launches) ──
    cross_token_funders: list[dict[str, Any]] = []
    if holder_addrs:
        try:
            funder_rows = await run_sync(
                _query_cross_token_funders,
                holder_addrs,
                token_address,
                chain_norm,
                _MAX_FUNDER_ROWS,
            )
            cross_token_funders = detect_cross_token_funder_graphs(funder_rows or [])
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            CHDriverError,
        ) as exc:
            logger.debug("risk_cluster_cross_token_funders_failed: %s", exc)
            cross_token_funders = []

    # ── Launch anchor (deploy block/ts) for temporal / sniper / MEV legs ──
    anchor_block: int | None = None
    anchor_ts: float | None = None
    try:
        anchor_block, anchor_ts = await _resolve_launch_anchor(token_address, chain_norm)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError):
        anchor_block, anchor_ts = None, None

    # ── Leg 5: temporal clusters (lockstep first buys) ──
    temporal_clusters: list[dict[str, Any]] = []
    try:
        buy_rows = await run_sync(_query_first_buy_blocks, token_address, chain_norm, _MAX_HOLDERS)
        temporal_clusters = detect_temporal_clusters(buy_rows or [], deploy_block=anchor_block)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("risk_cluster_temporal_failed: %s", exc)
        temporal_clusters = []

    # ── Legs 6+7: sniper/bot patterns + MEV bundles (launch-window rows) ──
    sniper_patterns: list[dict[str, Any]] = []
    mev_bundles: list[dict[str, Any]] = []
    if anchor_block is not None:
        try:
            launch_rows = await run_sync(
                _query_launch_window_transfers,
                token_address,
                chain_norm,
                anchor_block,
                _LAUNCH_WINDOW_BLOCKS,
                _MAX_LAUNCH_ROWS,
            )
            launch_rows = launch_rows or []
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            ValueError,
            KeyError,
            TypeError,
            CHDriverError,
        ) as exc:
            logger.debug("risk_cluster_launch_rows_failed: %s", exc)
            launch_rows = []

        if launch_rows:
            wallets = sorted({str(r[0]).lower() for r in launch_rows if r and r[0]})
            prior_wallets: set[str] = set()
            if wallets:
                try:
                    prior_rows = await run_sync(
                        _query_wallets_with_prior_tx,
                        wallets,
                        chain_norm,
                        anchor_ts,
                    )
                    prior_wallets = {str(r[0]).lower() for r in (prior_rows or []) if r and r[0]}
                except (
                    ConnectionError,
                    OSError,
                    TimeoutError,
                    ValueError,
                    KeyError,
                    TypeError,
                    CHDriverError,
                ):
                    prior_wallets = set()

            sniper_patterns = detect_sniper_bot_patterns(
                launch_rows,
                deploy_block=anchor_block,
                wallets_with_prior_tx=prior_wallets,
            )
            mev_bundles = detect_mev_bundles(launch_rows)

    # ── Leg 8: wash-trading clusters (circular transfer cycles, 72h window) ──
    wash_clusters: dict[str, Any] = {}
    try:
        transfer_rows = await run_sync(
            _query_token_transfers,
            token_address,
            chain_norm,
            _WASH_LOOKBACK_HOURS,
            _MAX_WASH_TRANSFER_ROWS,
        )
        wash_clusters = detect_wash_trading_clusters(transfer_rows or [], lookback_hours=_WASH_LOOKBACK_HOURS)
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError) as exc:
        logger.debug("risk_cluster_wash_failed: %s", exc)
        wash_clusters = {}

    # ── Composite cluster risk score ──
    bundle_score = 0.0
    if isinstance(bundle, dict):
        bundle_score = max(
            float(bundle.get("launch_cluster_score") or 0),
            float(bundle.get("common_funder_score") or 0),
            float(bundle.get("confidence") or 0) if bundle.get("is_bundled") else 0.0,
        )
    risk_score, risk_label, risk_description, component_scores = score_cluster_risk(
        funder_tree,
        shared_deployers,
        cross_token,
        bundle_score=bundle_score,
        cross_token_funders=cross_token_funders,
        temporal_clusters=temporal_clusters,
        sniper_patterns=sniper_patterns,
        mev_bundles=mev_bundles,
        wash_clusters=wash_clusters,
    )

    saw_data = (
        bool(funder_tree or shared_deployers or cross_token or cross_token_funders)
        or bool(temporal_clusters or sniper_patterns or mev_bundles)
        or bool(wash_clusters)
        or bundle_score > 0
    )
    if not saw_data:
        return _unavailable_result(
            token_address,
            chain_norm,
            reason="no cluster data in own indexer",
            deployer=deployer,
            holders_analyzed=len(holder_addrs),
        )

    _elapsed_ms = (time.monotonic() - _start) * 1000
    logger.info(
        "risk_cluster_latency",
        ms=round(_elapsed_ms, 1),
        token=token_address[:16],
        chain=chain_norm,
        funder_roots=len(funder_tree),
        shared_deployers=len(shared_deployers),
        cross_token=len(cross_token),
        cross_token_funders=len(cross_token_funders),
        temporal_clusters=len(temporal_clusters),
        sniper_patterns=len(sniper_patterns),
        mev_bundles=len(mev_bundles),
        wash_cycles=len((wash_clusters or {}).get("cycles") or []),
        risk_score=risk_score,
    )

    return {
        "check": "risk_cluster",
        "result": "scanned",
        "source": "own",
        "token_address": token_address,
        "chain": chain_norm,
        "deployer": deployer,
        "holders_analyzed": len(holder_addrs),
        "funder_tree": funder_tree,
        "shared_deployer_network": shared_deployers,
        "cross_token_clusters": cross_token,
        "cross_token_funders": cross_token_funders,
        "temporal_clusters": temporal_clusters,
        "sniper_patterns": sniper_patterns,
        "mev_bundles": mev_bundles,
        "wash_trading": wash_clusters,
        "cluster_risk_score": risk_score,
        "cluster_risk_label": risk_label,
        "cluster_risk_description": risk_description,
        "component_scores": component_scores,
        "note": (
            "funder-tree + shared-deployer network + cross-token cluster graph "
            "+ cross-token funder graphs + temporal lockstep + sniper/bot patterns "
            "+ MEV bundles + wash-trading clusters (beyond single-token clustering)"
        ),
    }
