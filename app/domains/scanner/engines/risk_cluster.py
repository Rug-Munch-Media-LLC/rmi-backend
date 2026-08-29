"""CLUSTER detection — wallet clustering beyond single-token BubbleMaps.

This module is the "cluster" engine for the structured ``RiskReport``. It goes
further than the market-standard single-token clustering (shared funder /
same-block buy / bundle groups — see ``holder_clusters.py``) by building EIGHT
graph structures that span LAUNCHES:

1. **Funder-tree** — the recursive funding tree for a token's top holders.
   Every top holder is connected to its first funder (``evm_transfers``); a
   funder that paid for >= 2 holders of THIS token is a tree root. When the
   root is the deployer (or a wallet that also funded other deployers) the
   cluster is a team / farm signal.

2. **Shared-deployer network** — deployers who launched multiple tokens whose
   top-holder sets overlap with this token's holders. A wallet set that
   re-appears across launches of the SAME deployer is a repeat-buyer ring
   (wash-deploy pattern: new token, same wallets).

3. **Cross-token cluster graph** — wallet groups that co-appeared as top
   holders in >= 2 OTHER launches. Wallets that move between launches
   together are coordinated (sybil / sniper ring), even when no single
   funder connects them.

4. **Cross-token funder graph** — funders who seeded wallets across >= 2 OTHER
   launches (a funder that pays for holders in many launches is the capital
   source behind a coordinated launch network). This goes one level above the
   funder tree: the tree finds the source WITHIN one token, the graph finds
   the source ACROSS the deployer's whole launch history.

5. **Temporal clusters (lockstep)** — wallets whose first buys land in the
   SAME block, or in a rapid run of consecutive launch blocks. Wallets that
   act in lockstep are coordinated even when they share no funder.

6. **Sniper / bot patterns** — launch-window signatures that a bot farm
   opened the book: same-block multi-wallet snipes, uniform (bot-sized)
   amounts, first-tx-ever wallets, and clustered gas tips.

7. **MEV-bundle detection** — atomic multi-wallet buys in one block with
   near-identical amounts (one searcher bundling many users), buy-then-
   distribute sequences (one buy, many recipients next block), and
   identical-tip groups consistent with a Jito / Flashbots-style bundle.

8. **Wash-trading clusters** — circular transfer cycles (A→B→A roundtrips,
   A→B→C→A multi-hop loops, cross-DEX loops) and self-swaps within a 72h
   coordination window. Cycles inflate volume and fake holder activity.

Every structure carries evidence (funder addresses, token addresses, wallet
addresses, blocks, txs) and feeds a single 0-100 ``cluster_risk_score``
(higher = riskier) with a per-component breakdown.

Data sources (all LOCAL, zero cost — the same ``wallet_memory`` tables the
other own-data engines use):

  - ``wallet_memory.evm_transfers``      — first funder per holder, cross-token
                                           holder sets, launch-window buys,
                                           transfer cycles (``to_address`` rows)
  - ``wallet_memory.dex_events``         — first-buy blocks for lockstep
                                           temporal clustering
  - ``wallet_memory.deployer_events``    — deploy block/ts (launch anchor),
                                           other tokens the deployer launched
  - ``wallet_memory.solana_spl_balance_changes`` — Solana holder sets

Best-effort by design: when ClickHouse is unavailable every query degrades to
an empty list and ``analyze_wallet_clusters`` returns a graceful
``unavailable`` payload — the scan never crashes on missing cluster data. The
pure graph builders (``build_funder_tree`` / ``detect_shared_deployer_networks``
/ ``detect_cross_token_clusters`` / ``detect_cross_token_funder_graphs`` /
``detect_temporal_clusters`` / ``detect_sniper_bot_patterns`` /
``detect_mev_bundles`` / ``detect_wash_trading_clusters`` /
``score_cluster_risk``) take plain dicts so they are unit-testable without a
database.

Usage:
    from app.domains.scanner.engines.risk_cluster import analyze_wallet_clusters

    result = await analyze_wallet_clusters(
        "0x...", "ethereum",
        deployer="0x...",
        holders=["0xA", "0xB", ...],
        bundle={...},  # own_bundle_detect output (optional)
    )
"""

from __future__ import annotations

import time
from typing import Any, cast

try:
    from clickhouse_driver.errors import Error as CHDriverError
except ImportError:  # pragma: no cover
    CHDriverError = Exception

from app.domains.scanner.engines._common import run_sync, to_epoch
from app.domains.scanner.engines._indexer_port import (
    ch_client as _get_ch_client,
    normalize_chain as _normalize_chain,
)
from app.domains.scanner.engines._logging import get_logger

logger = get_logger(__name__)

# ── Query / analysis limits (time-boxing — matches sibling engines) ───────
_QUERY_TIMEOUT_SECONDS = 10
_MAX_HOLDERS = 50  # top-N holders fed into the cross-token overlap scan
_MAX_OVERLAP_TOKENS = 20  # other tokens considered for cross-token clusters
_MIN_FUNDER_CHILDREN = 2  # funder with >= 2 holders of this token = tree root
_MIN_CROSS_TOKEN_MEMBERS = 2  # wallet group size for a cross-token cluster
_MIN_SHARED_DEPLOYER_MEMBERS = 2  # wallet overlap size for shared-deployer net

# ── Deepened cluster limits (legs 4-8) ────────────────────────────────────
_MAX_FUNDER_ROWS = 100  # funder×token rows fed into the cross-token funder graph
_MIN_CROSS_TOKEN_FUNDER_TOKENS = 2  # launches a funder must seed to be a graph node
_MIN_TEMPORAL_MEMBERS = 2  # wallets entering in the same block = lockstep
_TEMPORAL_BLOCK_WINDOW = 3  # consecutive blocks merged into a rapid sequence
_LAUNCH_WINDOW_BLOCKS = 30  # blocks after deploy treated as the launch window
_MAX_LAUNCH_ROWS = 200  # launch-window rows fed into sniper / MEV detection
_BOT_CV_THRESHOLD = 0.25  # coefficient of variation → uniform (bot) amounts
_MEV_AMOUNT_BAND = 0.30  # CV of atomic bundle-buy amounts
_MEV_TIP_BAND_PCT = 5.0  # tips within 5% of each other = same bundle
_WASH_LOOKBACK_HOURS = 72  # coordination window for wash-trade cycles
_MAX_WASH_TRANSFER_ROWS = 500  # transfer rows fed into wash-cycle detection
_MIN_WASH_CYCLE_LEN = 2  # shortest cycle (A→B→A roundtrip)
_MAX_WASH_CYCLE_LEN = 5  # longest cycle considered coordinated

_ZERO_ADDR = "0x0000000000000000000000000000000000000000"
_DEAD_ADDR = "0x000000000000000000000000000000000000dead"


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════════
# PURE GRAPH BUILDERS (unit-testable — no database)
# ═══════════════════════════════════════════════════════════════════════════


def build_funder_tree(
    first_in: list[dict[str, Any]] | list[tuple[Any, ...]],
    *,
    deployer: str | None = None,
) -> list[dict[str, Any]]:
    """Build the funder tree: funders that funded >= 2 holders of this token.

    ``first_in`` rows are either dicts (``holder`` / ``funder`` /
    ``first_tx``) or tuples ``(holder, funder, first_tx, ...)`` — both shapes
    appear across engine outputs. Pool / token-contract funders are excluded
    because they cluster every buyer and are not meaningful.

    Returns roots sorted by ``share_pct`` (largest first). Each root carries
    the funded holder addresses, the holder count, the funder's share of the
    analyzed holder set, and the funding tx evidence.
    """
    records: list[dict[str, Any]] = []
    for row in first_in:
        if isinstance(row, dict):
            holder = str(row.get("holder") or row.get("address") or "").lower()
            funder = str(row.get("funder") or "").lower() or None
            tx = row.get("first_tx") or row.get("first_funding_tx") or None
        elif isinstance(row, (tuple, list)) and len(row) >= 2:
            holder = str(row[0] or "").lower()
            funder = str(row[1] or "").lower() or None
            tx = str(row[2] or "") if len(row) > 2 else None
        else:
            continue
        if not holder or not funder:
            continue
        if funder in (_ZERO_ADDR, _DEAD_ADDR):
            continue
        records.append({"holder": holder, "funder": funder, "tx": tx})

    if not records:
        return []

    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        groups.setdefault(str(rec["funder"]), []).append(rec)

    deployer_l = (deployer or "").lower()
    tree: list[dict[str, Any]] = []
    for funder, recs in groups.items():
        if len(recs) < _MIN_FUNDER_CHILDREN:
            continue
        is_deployer = bool(deployer_l) and funder == deployer_l
        tree.append(
            {
                "funder": funder,
                "funder_is_deployer": is_deployer,
                "holders": [r["holder"] for r in recs],
                "holder_count": len(recs),
                "share_pct": round(len(recs) / len(records) * 100, 1),
                "evidence": [r["tx"] for r in recs if r["tx"]][:5],
            }
        )

    return sorted(tree, key=lambda c: c["share_pct"], reverse=True)


def detect_shared_deployer_networks(
    overlap_rows: list[dict[str, Any]] | list[tuple[Any, ...]],
    deployer: str | None = None,
) -> list[dict[str, Any]]:
    """Group other tokens launched by the same deployer by shared wallet set.

    ``overlap_rows``: per-other-token rows with the token address, deployer,
    and the wallets of ours that also hold it. A deployer with >= 2 launches
    sharing >= 2 of this token's wallets is a repeat-buyer ring.
    """
    by_token: dict[str, dict[str, Any]] = {}
    for row in overlap_rows:
        if isinstance(row, dict):
            token = str(row.get("token_address") or row.get("token") or "").lower()
            tok_deployer = str(row.get("deployer") or "").lower() or None
            wallets = row.get("wallets") or []
            if isinstance(wallets, str):
                wallets = [wallets]
            wallets = [str(w).lower() for w in wallets if w]
        elif isinstance(row, (tuple, list)) and len(row) >= 3:
            token = str(row[0] or "").lower()
            tok_deployer = str(row[1] or "").lower() or None
            wallets = [str(w).lower() for w in (row[2:] or []) if w]
        else:
            continue
        if not token:
            continue
        by_token[token] = {"token": token, "deployer": tok_deployer, "wallets": wallets}

    if not by_token:
        return []

    # Group tokens by deployer.
    by_deployer: dict[str, list[dict[str, Any]]] = {}
    for tok in by_token.values():
        d = str(tok.get("deployer") or deployer or "unknown")
        by_deployer.setdefault(d, []).append(tok)

    networks: list[dict[str, Any]] = []
    for d, tokens in by_deployer.items():
        if len(tokens) < 2:
            continue
        # Wallets that appear in 2+ of this deployer's launches.
        wallet_seen: dict[str, int] = {}
        for tok in tokens:
            for w in tok["wallets"]:
                wallet_seen[w] = wallet_seen.get(w, 0) + 1
        shared = sorted([w for w, n in wallet_seen.items() if n >= _MIN_SHARED_DEPLOYER_MEMBERS])
        if len(shared) < _MIN_SHARED_DEPLOYER_MEMBERS:
            continue
        networks.append(
            {
                "deployer": d,
                "tokens": [t["token"] for t in tokens],
                "launch_count": len(tokens),
                "shared_wallets": shared,
                "overlap": round(len(shared) / max(len(shared), 1) * 100, 1),
                "note": f"{len(shared)} wallets re-appear across {len(tokens)} launches by the same deployer",
            }
        )

    return sorted(networks, key=lambda n: len(n["shared_wallets"]), reverse=True)


def detect_cross_token_clusters(
    overlap_rows: list[dict[str, Any]] | list[tuple[Any, ...]],
) -> list[dict[str, Any]]:
    """Detect wallet groups that co-appeared as holders of other launches.

    ``overlap_rows``: per-other-token rows with the token address and the
    wallets of ours that also hold it. A group of >= 2 wallets appearing
    together in another launch is a coordinated cluster (sybil / sniper ring),
    even without a shared funder.
    """
    clusters: list[dict[str, Any]] = []
    for row in overlap_rows:
        if isinstance(row, dict):
            token = str(row.get("token_address") or row.get("token") or "").lower()
            wallets = row.get("wallets") or []
            if isinstance(wallets, str):
                wallets = [wallets]
            wallets = [str(w).lower() for w in wallets if w]
        elif isinstance(row, (tuple, list)) and len(row) >= 2:
            token = str(row[0] or "").lower()
            wallets = [str(w).lower() for w in (row[1:] or []) if w]
        else:
            continue
        if not token or len(wallets) < _MIN_CROSS_TOKEN_MEMBERS:
            continue
        clusters.append(
            {
                "token": token,
                "wallets": wallets,
                "members": len(wallets),
                "note": f"{len(wallets)} of this token's holders co-hold {token[:10]}…",
            }
        )

    return sorted(clusters, key=lambda c: c["members"], reverse=True)


def detect_cross_token_funder_graphs(
    overlap_rows: list[dict[str, Any]] | list[tuple[Any, ...]],
) -> list[dict[str, Any]]:
    """Build the cross-token funder graph: funders seeding >= 2 other launches.

    ``overlap_rows``: per-(funder, other-token) rows with the funder address,
    the other token address, and the wallets that funder paid for in that
    token. A funder that appears across >= 2 OTHER launches is a graph node —
    the capital source behind a coordinated launch network. This is the
    ACROSS-launch analogue of the single-token funder tree.

    Returns nodes sorted by ``launch_count`` (largest first), each with the
    seeded tokens, funded wallet set, and evidence note.
    """
    by_funder: dict[str, dict[str, list[str]]] = {}
    for row in overlap_rows:
        if isinstance(row, dict):
            funder = str(row.get("funder") or row.get("from_address") or "").lower()
            token = str(row.get("token_address") or row.get("token") or "").lower()
            wallets = row.get("wallets") or row.get("funded_wallets") or []
            if isinstance(wallets, str):
                wallets = [wallets]
            wallets = [str(w).lower() for w in wallets if w]
        elif isinstance(row, (tuple, list)) and len(row) >= 2:
            funder = str(row[0] or "").lower()
            token = str(row[1] or "").lower()
            wallets = [str(w).lower() for w in (row[2:] or []) if w]
        else:
            continue
        if not funder or not token or funder in (_ZERO_ADDR, _DEAD_ADDR):
            continue
        by_funder.setdefault(funder, {}).setdefault(token, [])
        for w in wallets:
            if w and w not in by_funder[funder][token]:
                by_funder[funder][token].append(w)

    graphs: list[dict[str, Any]] = []
    for funder, tokens in by_funder.items():
        if len(tokens) < _MIN_CROSS_TOKEN_FUNDER_TOKENS:
            continue
        funded_wallets = sorted({w for ws in tokens.values() for w in ws})
        graphs.append(
            {
                "funder": funder,
                "tokens": sorted(tokens.keys()),
                "launch_count": len(tokens),
                "funded_wallet_count": len(funded_wallets),
                "funded_wallets": funded_wallets[:20],
                "note": f"funder seeded wallets in {len(tokens)} other launches",
            }
        )

    return sorted(graphs, key=lambda g: (g["launch_count"], g["funded_wallet_count"]), reverse=True)


def detect_temporal_clusters(
    first_buys: list[dict[str, Any]] | list[tuple[Any, ...]],
    *,
    deploy_block: int | None = None,
) -> list[dict[str, Any]]:
    """Detect lockstep wallet groups: holders entering in the same block.

    ``first_buys`` rows are dicts (``wallet`` / ``address``, ``block`` /
    ``block_number``, optional ``tx``) or tuples ``(wallet, block, ...)``.
    Two signals are produced:

      * exact **lockstep**: >= 2 wallets whose FIRST buy landed in the same
        block (span 0);
      * **rapid sequence**: >= 3 wallets entering across a run of consecutive
        blocks within ``_TEMPORAL_BLOCK_WINDOW`` blocks of each other.

    Wallets that act in lockstep are coordinated even when no single funder
    connects them. Each cluster carries the entry block, the wallet set, an
    ``early_launch`` flag (entry inside the first ``_LAUNCH_WINDOW_BLOCKS``
    after deploy — i.e. a launch-window snipe group) and an evidence note.
    """
    by_block: dict[int, dict[str, dict[str, Any]]] = {}
    for row in first_buys:
        if isinstance(row, dict):
            wallet = str(row.get("wallet") or row.get("address") or row.get("buyer") or "").lower()
            block = row.get("block", row.get("block_number"))
            tx = row.get("tx") or row.get("first_tx") or None
        elif isinstance(row, (tuple, list)) and len(row) >= 2:
            wallet = str(row[0] or "").lower()
            block = row[1]
            tx = str(row[2]) if len(row) > 2 and row[2] else None
        else:
            continue
        if not wallet or block is None:
            continue
        try:
            block_i = int(block)
        except (TypeError, ValueError):
            continue
        by_block.setdefault(block_i, {})
        by_block[block_i][wallet] = {"wallet": wallet, "tx": tx}

    if not by_block:
        return []

    clusters: list[dict[str, Any]] = []

    def _is_early(block: int) -> bool:
        return deploy_block is not None and block <= int(deploy_block) + _LAUNCH_WINDOW_BLOCKS

    # 1. Exact same-block lockstep groups.
    for block in sorted(by_block):
        wallets = sorted(by_block[block].keys())
        if len(wallets) < _MIN_TEMPORAL_MEMBERS:
            continue
        clusters.append(
            {
                "block": block,
                "wallets": wallets,
                "members": len(wallets),
                "span_blocks": 0,
                "rapid_sequence": False,
                "early_launch": _is_early(block),
                "note": f"{len(wallets)} wallets entered in the same block ({block})",
            }
        )

    # 2. Rapid sequences: runs of consecutive blocks within the window.
    ordered = sorted(by_block)
    run: list[int] = []
    for block in ordered:
        if run and block - run[-1] > _TEMPORAL_BLOCK_WINDOW:
            run = []
        run.append(block)
        if len(run) < 2:
            continue
        run_wallets = sorted({w for b in run for w in by_block[b]})
        span = run[-1] - run[0]
        if len(run_wallets) >= 3 and span > 0:
            clusters.append(
                {
                    "block": run[-1],
                    "wallets": run_wallets,
                    "members": len(run_wallets),
                    "span_blocks": span,
                    "rapid_sequence": True,
                    "early_launch": _is_early(run[0]),
                    "note": f"{len(run_wallets)} wallets entered within {span} block(s) ({run[0]}–{run[-1]})",
                }
            )

    return sorted(clusters, key=lambda c: c["members"], reverse=True)


def detect_sniper_bot_patterns(
    launch_rows: list[dict[str, Any]] | list[tuple[Any, ...]],
    *,
    deploy_block: int | None = None,
    wallets_with_prior_tx: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Detect sniper / bot-launch signatures in the launch window.

    ``launch_rows`` are dicts (``wallet`` / ``address``, ``amount``,
    ``block`` / ``block_number``, optional ``tx`` / ``tip``) or tuples
    ``(wallet, amount, block, ...)``. ``wallets_with_prior_tx`` (optional)
    marks wallets that existed on-chain before the launch — anything absent
    from that set is a first-tx-ever wallet (a bot-farm signature).

    Signals (each returned as one evidence entry):

      * ``same_block_snipe`` — >= 3 distinct wallets bought in one launch block;
      * ``uniform_bot_amounts`` — coefficient of variation of buy amounts
        < ``_BOT_CV_THRESHOLD`` (bot-sized, identical amounts);
      * ``fresh_wallet_bots`` — >= 3 first-tx-ever wallets buying at launch;
      * ``tip_cluster`` — >= 2 wallets paying near-identical gas tips
        (``_MEV_TIP_BAND_PCT``) — the same automation;
      * ``sniper_wave`` — >= 5 wallets entering inside the early-launch window.
    """
    rows: list[dict[str, Any]] = []
    for row in launch_rows:
        if isinstance(row, dict):
            wallet = str(row.get("wallet") or row.get("address") or row.get("buyer") or "").lower()
            amount = row.get("amount", row.get("value"))
            block = row.get("block", row.get("block_number"))
            tx = row.get("tx") or row.get("tx_hash") or None
            tip = row.get("tip") or row.get("gas_tip")
        elif isinstance(row, (tuple, list)) and len(row) >= 2:
            wallet = str(row[0] or "").lower()
            amount = row[1]
            block = row[2] if len(row) > 2 else None
            tx = str(row[3]) if len(row) > 3 and row[3] else None
            tip = row[4] if len(row) > 4 else None
        else:
            continue
        if not wallet:
            continue
        try:
            amt = float(amount) if amount is not None else 0.0
        except (TypeError, ValueError):
            amt = 0.0
        try:
            blk = int(block) if block is not None else None
        except (TypeError, ValueError):
            blk = None
        try:
            tip_f = float(tip) if tip is not None else None
        except (TypeError, ValueError):
            tip_f = None
        rows.append({"wallet": wallet, "amount": amt, "block": blk, "tx": tx, "tip": tip_f})

    if not rows:
        return []

    # Dedupe by wallet keeping the earliest (lowest block) row.
    rows.sort(key=lambda r: (r["block"] if r["block"] is not None else 0, r["amount"]))
    earliest: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["wallet"] not in earliest:
            earliest[r["wallet"]] = r

    wallets = list(earliest.keys())
    amounts = [earliest[w]["amount"] for w in wallets]

    patterns: list[dict[str, Any]] = []

    # 1. Same-block snipe: >= 3 distinct wallets in one launch block.
    by_block: dict[int, list[str]] = {}
    for w in wallets:
        b = earliest[w]["block"]
        if b is not None:
            by_block.setdefault(b, []).append(w)
    for block in sorted(by_block):
        members = by_block[block]
        if len(members) >= 3:
            patterns.append(
                {
                    "pattern": "same_block_snipe",
                    "block": block,
                    "wallets": members,
                    "members": len(members),
                    "detail": f"{len(members)} wallets bought in the same launch block ({block})",
                }
            )

    # 2. Uniform (bot-sized) amounts: low coefficient of variation.
    if len(amounts) >= 3:
        mean = sum(amounts) / len(amounts)
        if mean > 0:
            variance = sum((a - mean) ** 2 for a in amounts) / len(amounts)
            cv = (variance**0.5) / mean
            if cv < _BOT_CV_THRESHOLD:
                patterns.append(
                    {
                        "pattern": "uniform_bot_amounts",
                        "cv": round(cv, 3),
                        "wallets": wallets,
                        "members": len(wallets),
                        "detail": f"{len(wallets)} launch buys are uniform (CV {cv:.2f} < {_BOT_CV_THRESHOLD:.2f})",
                    }
                )

    # 3. Fresh-wallet bots: first-tx-ever wallets buying at launch.
    if wallets_with_prior_tx is not None:
        fresh = [w for w in wallets if w not in wallets_with_prior_tx]
        if len(fresh) >= 3:
            patterns.append(
                {
                    "pattern": "fresh_wallet_bots",
                    "wallets": fresh,
                    "members": len(fresh),
                    "detail": f"{len(fresh)} launch buyers have no on-chain history before this token",
                }
            )

    # 4. Tip clustering: near-identical gas tips in the same launch block.
    tips: dict[tuple[int | None, float], list[str]] = {}
    for w in wallets:
        t = earliest[w]["tip"]
        b = earliest[w]["block"]
        if t is None:
            continue
        placed = False
        for (band_block, band_tip), members in tips.items():
            if band_block == b and abs(band_tip - t) / max(band_tip, 1e-12) <= _MEV_TIP_BAND_PCT / 100.0:
                members.append(w)
                placed = True
                break
        if not placed:
            tips[(b, t)] = [w]
    for (band_block, band_tip), members in tips.items():
        if len(members) >= 2:
            patterns.append(
                {
                    "pattern": "tip_cluster",
                    "block": band_block,
                    "wallets": members,
                    "members": len(members),
                    "detail": f"{len(members)} wallets paid near-identical tips ({band_tip:.6g}) in the same block",
                }
            )

    # 5. Sniper wave: >= 5 wallets inside the early-launch window.
    if deploy_block is not None:
        early = [
            w
            for w in wallets
            if earliest[w]["block"] is not None and earliest[w]["block"] <= int(deploy_block) + _LAUNCH_WINDOW_BLOCKS
        ]
        if len(early) >= 5:
            patterns.append(
                {
                    "pattern": "sniper_wave",
                    "wallets": early,
                    "members": len(early),
                    "detail": f"{len(early)} wallets entered within {_LAUNCH_WINDOW_BLOCKS} blocks of deployment",
                }
            )

    # Stable ordering: signal strength first, then member count.
    _SIGNAL_ORDER = {
        "sniper_wave": 0,
        "same_block_snipe": 1,
        "fresh_wallet_bots": 2,
        "uniform_bot_amounts": 3,
        "tip_cluster": 4,
    }
    return sorted(
        patterns,
        key=lambda p: (_SIGNAL_ORDER.get(str(p.get("pattern") or ""), 9), -int(p.get("members") or 0)),
    )


def detect_mev_bundles(
    transfer_rows: list[dict[str, Any]] | list[tuple[Any, ...]],
) -> list[dict[str, Any]]:
    """Detect MEV-bundle signatures in the launch-window transfer feed.

    ``transfer_rows`` are dicts (``block`` / ``block_number``, ``tx`` /
    ``tx_hash``, ``from`` / ``from_address``, ``to`` / ``to_address``,
    ``amount`` / ``value``, optional ``tip``) or tuples
    ``(block, tx, from, to, amount, tip?)``.

    Signals:

      * ``atomic_bundle_buy`` — >= 2 distinct receivers in ONE block whose buy
        amounts sit within ``_MEV_AMOUNT_BAND`` of each other (a searcher
        bundling multiple users into a single block);
      * ``buy_then_distribute`` — a wallet that bought in block N and then
        sent to >= 2 distinct receivers within the next block (bundle
        distribution: one buy, many wallets);
      * ``identical_tip_bundle`` — >= 2 wallets in one block paying exactly
        the same tip (Jito / Flashbots-style atomic bundle tip).
    """
    rows: list[dict[str, Any]] = []
    for row in transfer_rows:
        if isinstance(row, dict):
            block = row.get("block", row.get("block_number"))
            tx = row.get("tx") or row.get("tx_hash") or None
            src = str(row.get("from") or row.get("from_address") or "").lower()
            dst = str(row.get("to") or row.get("to_address") or "").lower()
            amount = row.get("amount", row.get("value"))
            tip = row.get("tip") or row.get("gas_tip")
        elif isinstance(row, (tuple, list)) and len(row) >= 5:
            block = row[0]
            tx = str(row[1]) if row[1] else None
            src = str(row[2] or "").lower()
            dst = str(row[3] or "").lower()
            amount = row[4]
            tip = row[5] if len(row) > 5 else None
        else:
            continue
        if not src or not dst or dst in (_ZERO_ADDR, _DEAD_ADDR):
            continue
        try:
            blk = int(block) if block is not None else None
        except (TypeError, ValueError):
            blk = None
        try:
            amt = float(amount) if amount is not None else 0.0
        except (TypeError, ValueError):
            amt = 0.0
        try:
            tip_f = float(tip) if tip is not None else None
        except (TypeError, ValueError):
            tip_f = None
        rows.append({"block": blk, "tx": tx, "from": src, "to": dst, "amount": amt, "tip": tip_f})

    if not rows:
        return []

    bundles: list[dict[str, Any]] = []

    # 1. Atomic bundle buys: one block, >= 2 receivers, tight amount band.
    by_block: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        if r["block"] is not None:
            by_block.setdefault(r["block"], []).append(r)
    for block in sorted(by_block):
        receivers: dict[str, list[dict[str, Any]]] = {}
        for r in by_block[block]:
            receivers.setdefault(r["to"], []).append(r)
        if len(receivers) < 2:
            continue
        amounts = [sum(r["amount"] for r in recs) for recs in receivers.values()]
        mean = sum(amounts) / len(amounts)
        if mean <= 0:
            continue
        variance = sum((a - mean) ** 2 for a in amounts) / len(amounts)
        cv = (variance**0.5) / mean
        if cv < _MEV_AMOUNT_BAND:
            bundles.append(
                {
                    "pattern": "atomic_bundle_buy",
                    "block": block,
                    "wallets": sorted(receivers.keys()),
                    "members": len(receivers),
                    "tx_hashes": [r["tx"] for r in by_block[block] if r["tx"]][:10],
                    "detail": f"{len(receivers)} wallets bought in one block with uniform amounts (CV {cv:.2f})",
                }
            )

    # 2. Buy-then-distribute: a wallet that bought and split to >= 2 receivers.
    sends_by_wallet: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["from"] and r["from"] not in (_ZERO_ADDR, _DEAD_ADDR):
            sends_by_wallet.setdefault(r["from"], []).append(r)
    for wallet, sends in sends_by_wallet.items():
        by_block_s: dict[int, list[dict[str, Any]]] = {}
        for s in sends:
            if s["block"] is not None:
                by_block_s.setdefault(s["block"], []).append(s)
        ordered_blocks = sorted(by_block_s)
        for idx, block in enumerate(ordered_blocks):
            if idx + 1 >= len(ordered_blocks):
                break
            nxt = ordered_blocks[idx + 1]
            if nxt - block > 1:
                continue
            receiver_set = {s["to"] for s in by_block_s.get(nxt, []) if s["to"]}
            if len(receiver_set) >= 2:
                bundles.append(
                    {
                        "pattern": "buy_then_distribute",
                        "block": block,
                        "wallet": wallet,
                        "receivers": sorted(receiver_set),
                        "members": len(receiver_set),
                        "detail": f"{wallet[:10]}… bought then split to {len(receiver_set)} wallets in block {nxt}",
                    }
                )

    # 3. Identical-tip bundle: same block, same exact tip, >= 2 wallets.
    tip_groups: dict[tuple[int, float], list[str]] = {}
    for r in rows:
        if r["tip"] is None or r["block"] is None:
            continue
        tip_groups.setdefault((r["block"], r["tip"]), []).append(r["to"])
    for (block, tip), wallets in tip_groups.items():
        unique_wallets = sorted(set(wallets))
        if len(unique_wallets) >= 2:
            bundles.append(
                {
                    "pattern": "identical_tip_bundle",
                    "block": block,
                    "wallets": unique_wallets,
                    "members": len(unique_wallets),
                    "detail": f"{len(unique_wallets)} wallets paid the identical tip ({tip:.6g}) in one block",
                }
            )

    _MEV_ORDER = {"identical_tip_bundle": 0, "atomic_bundle_buy": 1, "buy_then_distribute": 2}
    return sorted(
        bundles,
        key=lambda b: (_MEV_ORDER.get(str(b.get("pattern") or ""), 9), -int(b.get("members") or 0)),
    )


def detect_wash_trading_clusters(
    transfer_rows: list[dict[str, Any]] | list[tuple[Any, ...]],
    *,
    lookback_hours: int = _WASH_LOOKBACK_HOURS,
) -> dict[str, Any]:
    """Detect wash-trading clusters: circular transfer cycles + self-swaps.

    ``transfer_rows`` are dicts (``from`` / ``from_address``, ``to`` /
    ``to_address``, ``amount`` / ``value``, optional ``timestamp`` /
    ``block_timestamp``, optional ``dex``) or tuples
    ``(from, to, amount, timestamp, dex?)``. Rows whose sender == receiver are
    reported separately as self-swaps.

    Cycles are enumerated by a dependency-free depth-first search (no
    networkx) over the directed transfer graph, limited to
    ``_MAX_WASH_CYCLE_LEN`` hops and the ``lookback_hours`` coordination
    window (mirrors the SENTINEL wash-trading detector's 72h rule and
    confidence model). Returns a summary dict::

        {
            "cycles": [{"wallets", "cycle_length", "combined_volume",
                        "time_span_hours", "confidence", "pattern_type",
                        "evidence"}],
            "self_swaps": [{"wallet", "count", "volume"}],
            "total_transfer_volume": float,
            "cycle_volume": float,
            "cycle_volume_ratio": float,   # 0-1 of volume in cycles
            "wash_score": 0-100,
        }
    """
    rows: list[dict[str, Any]] = []
    for row in transfer_rows:
        if isinstance(row, dict):
            src = str(row.get("from") or row.get("from_address") or "").lower()
            dst = str(row.get("to") or row.get("to_address") or "").lower()
            amount = row.get("amount", row.get("value"))
            ts = row.get("timestamp", row.get("block_timestamp"))
            dex = str(row.get("dex") or row.get("pool") or "")
        elif isinstance(row, (tuple, list)) and len(row) >= 3:
            src = str(row[0] or "").lower()
            dst = str(row[1] or "").lower()
            amount = row[2]
            ts = row[3] if len(row) > 3 else None
            dex = str(row[4]) if len(row) > 4 and row[4] else ""
        else:
            continue
        if not src or not dst or src in (_ZERO_ADDR, _DEAD_ADDR) or dst in (_ZERO_ADDR, _DEAD_ADDR):
            continue
        try:
            amt = float(amount) if amount is not None else 0.0
        except (TypeError, ValueError):
            amt = 0.0
        try:
            ts_f = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts_f = None
        rows.append({"from": src, "to": dst, "amount": amt, "ts": ts_f, "dex": dex})

    if not rows:
        return _empty_wash_result()

    total_volume = sum(r["amount"] for r in rows)

    # Self-swaps: sender == receiver.
    self_swaps: dict[str, dict[str, Any]] = {}
    normal_rows: list[dict[str, Any]] = []
    for r in rows:
        if r["from"] == r["to"]:
            entry = self_swaps.setdefault(r["from"], {"wallet": r["from"], "count": 0, "volume": 0.0})
            entry["count"] += 1
            entry["volume"] += r["amount"]
        else:
            normal_rows.append(r)

    # Directed adjacency with edge metadata.
    adj: dict[str, list[dict[str, Any]]] = {}
    for r in normal_rows:
        adj.setdefault(r["from"], []).append(r)

    cycles: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    def _cycle_key(wallets: list[str]) -> str:
        return ",".join(sorted(wallets))

    def _walk(start: str, path: list[str], visited: set[str]) -> None:
        last = path[-1]
        for edge in adj.get(last, []):
            nxt = edge["to"]
            if nxt == start and len(path) >= _MIN_WASH_CYCLE_LEN:
                key = _cycle_key(path)
                if key not in seen_keys:
                    seen_keys.add(key)
                    cycles.append(_describe_cycle(path, adj))
                continue
            if nxt in visited or len(path) >= _MAX_WASH_CYCLE_LEN:
                continue
            visited.add(nxt)
            _walk(start, path + [nxt], visited)
            visited.remove(nxt)

    for start in sorted(adj):
        _walk(start, [start], {start})

    # Keep only cycles within the coordination window; compute volume ratio.
    kept: list[dict[str, Any]] = []
    cycle_volume = 0.0
    for c in cycles:
        span = c.get("time_span_hours")
        if span is None:
            continue  # mixed known/unknown timestamps — unverifiable, skip
        if span > lookback_hours:
            continue
        kept.append(c)
        cycle_volume += float(c.get("combined_volume") or 0.0)

    wash_score = min(100, (cycle_volume / total_volume * 100) * 2) if total_volume > 0 else 0
    return {
        "cycles": sorted(kept, key=lambda c: float(c.get("combined_volume") or 0.0), reverse=True),
        "self_swaps": sorted(self_swaps.values(), key=lambda s: s["volume"], reverse=True),
        "total_transfer_volume": round(total_volume, 4),
        "cycle_volume": round(cycle_volume, 4),
        "cycle_volume_ratio": round(cycle_volume / total_volume, 4) if total_volume > 0 else 0.0,
        "wash_score": int(round(wash_score)),
    }


def _describe_cycle(
    wallets: list[str],
    adj: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Build a wash-cycle dict from a wallet path (uses the max-amount edge per hop)."""
    volumes: list[float] = []
    timestamps: list[float] = []
    dexes: set[str] = set()
    for i, wallet in enumerate(wallets):
        nxt = wallets[(i + 1) % len(wallets)]
        candidates = [e for e in adj.get(wallet, []) if e["to"] == nxt]
        if not candidates:
            continue
        edge = max(candidates, key=lambda e: e["amount"])
        volumes.append(edge["amount"])
        if edge["ts"] is not None:
            timestamps.append(edge["ts"])
        if edge["dex"]:
            dexes.add(edge["dex"])

    # Time-window handling mirrors the SENTINEL wash detector: mixed
    # known/unknown timestamps make the span unverifiable → caller skips.
    valid = [t for t in timestamps if t is not None]
    if valid and len(valid) == len(timestamps):
        span_hours = (max(valid) - min(valid)) / 3_600_000.0
    elif not timestamps:
        span_hours = 0.0
    else:
        span_hours = None  # unverifiable

    if len(dexes) > 1:
        pattern_type = "cross_dex"
    elif len(wallets) == 2:
        pattern_type = "simple_roundtrip"
    else:
        pattern_type = "multi_hop"

    confidence = 0.5
    if span_hours is not None and span_hours < 1:
        confidence += 0.3
    elif span_hours is not None and span_hours < 6:
        confidence += 0.2
    if len(wallets) == 2:
        confidence += 0.1
    confidence = min(0.99, confidence)

    return {
        "wallets": wallets,
        "cycle_length": len(wallets),
        "combined_volume": round(sum(volumes) / 2, 4),
        "time_span_hours": round(span_hours, 2) if span_hours is not None else None,
        "confidence": round(confidence, 2),
        "pattern_type": pattern_type,
        "evidence": [
            f"{len(wallets)}-wallet {pattern_type}",
            f"Involves {len(dexes)} DEX(es): {', '.join(sorted(dexes)) or 'unknown'}",
        ],
    }


def _empty_wash_result() -> dict[str, Any]:
    """Graceful empty wash result (no rows at all)."""
    return {
        "cycles": [],
        "self_swaps": [],
        "total_transfer_volume": 0.0,
        "cycle_volume": 0.0,
        "cycle_volume_ratio": 0.0,
        "wash_score": 0,
    }


def score_cluster_risk(
    funder_tree: list[dict[str, Any]],
    shared_deployers: list[dict[str, Any]],
    cross_token: list[dict[str, Any]],
    *,
    bundle_score: float = 0.0,
    cross_token_funders: list[dict[str, Any]] | None = None,
    temporal_clusters: list[dict[str, Any]] | None = None,
    sniper_patterns: list[dict[str, Any]] | None = None,
    mev_bundles: list[dict[str, Any]] | None = None,
    wash_clusters: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> tuple[int, str, str, dict[str, float]]:
    """Combine the eight cluster structures into a 0-100 cluster risk score.

    Higher = riskier. Scoring model (each contribution is capped):

      * deployer-funded tree roots        → +30 each (capped +60)
      * shared-funder tree roots          → +15 each (capped +30)
      * shared-deployer networks          → +25 each (capped +50)
      * cross-token clusters              → +15 each (capped +45)
      * cross-token funder graphs         → +20 each (capped +40)
      * temporal lockstep clusters        → +18 each (capped +36)
      * sniper/bot patterns               → +10..+25 each (capped +50)
      * MEV bundles                       → +25 each (capped +50)
      * wash-trade cycles + cycle-volume  → up to +40 (12/cycle + ratio*60)
      * confirmed launch bundle           → up to +40 (proportional to score)

    Returns ``(risk_score, risk_label, risk_description, component_scores)``
    with the label in LOW / MEDIUM / HIGH / CRITICAL and a per-structure
    point breakdown for transparency.
    """
    score = 0.0
    components: dict[str, float] = {}

    # ── Leg 1: funder tree ──
    deployer_roots = 0
    shared_roots = 0
    for root in funder_tree:
        if root.get("funder_is_deployer"):
            deployer_roots += 1
        else:
            shared_roots += 1
    tree_pts = min(30.0 * deployer_roots, 60.0) + min(15.0 * shared_roots, 30.0)
    score += tree_pts
    components["funder_tree"] = round(tree_pts, 1)

    # ── Leg 2: shared-deployer networks ──
    nets_pts = min(25.0 * len(shared_deployers), 50.0)
    score += nets_pts
    components["shared_deployer_network"] = round(nets_pts, 1)

    # ── Leg 3: cross-token clusters ──
    cross_pts = min(15.0 * len(cross_token), 45.0)
    score += cross_pts
    components["cross_token_clusters"] = round(cross_pts, 1)

    # ── Leg 4: cross-token funder graphs ──
    ctf = cross_token_funders or []
    ctf_pts = min(20.0 * len(ctf), 40.0)
    score += ctf_pts
    components["cross_token_funders"] = round(ctf_pts, 1)

    # ── Leg 5: temporal lockstep clusters ──
    temp = temporal_clusters or []
    temp_pts = min(18.0 * len(temp), 36.0)
    score += temp_pts
    components["temporal_clusters"] = round(temp_pts, 1)

    # ── Leg 6: sniper / bot patterns ──
    sniper_pts = 0.0
    for p in sniper_patterns or []:
        ptype = p.get("pattern")
        if ptype == "sniper_wave":
            sniper_pts += 25.0
        elif ptype == "same_block_snipe":
            sniper_pts += 20.0
        elif ptype in ("uniform_bot_amounts", "fresh_wallet_bots"):
            sniper_pts += 15.0
        elif ptype == "tip_cluster":
            sniper_pts += 10.0
        else:
            sniper_pts += 10.0
    sniper_pts = min(sniper_pts, 50.0)
    score += sniper_pts
    components["sniper_patterns"] = round(sniper_pts, 1)

    # ── Leg 7: MEV bundles ──
    mev_pts = min(25.0 * len(mev_bundles or []), 50.0)
    score += mev_pts
    components["mev_bundles"] = round(mev_pts, 1)

    # ── Leg 8: wash-trading clusters ──
    wash_cycles: list[dict[str, Any]] = []
    wash_ratio = 0.0
    if isinstance(wash_clusters, dict):
        wash_cycles = list(wash_clusters.get("cycles") or [])
        ratio = wash_clusters.get("cycle_volume_ratio")
        if isinstance(ratio, (int, float)):
            wash_ratio = float(ratio)
    elif isinstance(wash_clusters, list):
        wash_cycles = wash_clusters
    wash_pts = min(12.0 * len(wash_cycles) + 60.0 * wash_ratio, 40.0)
    score += wash_pts
    components["wash_trading"] = round(wash_pts, 1)

    # ── Confirmed launch bundle (from own_bundle_detect) ──
    if bundle_score > 0:
        bundle_pts = min(40.0, bundle_score * 0.4)
        score += bundle_pts
        components["bundle"] = round(bundle_pts, 1)

    score = max(0.0, min(100.0, score))
    risk_int = int(round(score))

    if risk_int >= 80:
        label = "CRITICAL"
        description = (
            "Extreme wallet coordination: deployer-funded trees, repeat-buyer rings, "
            "cross-token funder graphs, lockstep entries, MEV bundles, wash cycles "
            "and/or a confirmed launch bundle."
        )
    elif risk_int >= 55:
        label = "HIGH"
        description = (
            "Strong wallet coordination: shared funders, wallets that re-appear "
            "together across launches, lockstep snipes and/or MEV/wash signatures."
        )
    elif risk_int >= 30:
        label = "MEDIUM"
        description = (
            "Notable wallet clustering: some holders share a funder or co-hold "
            "other tokens, but no strong coordinated-launch pattern."
        )
    else:
        label = "LOW"
        description = "No meaningful wallet clustering detected beyond normal market behaviour."

    return risk_int, label, description, components


# ═══════════════════════════════════════════════════════════════════════════
# CLICKHOUSE QUERIES (sync, bounded — degrade to [] on failure)
# ═══════════════════════════════════════════════════════════════════════════


def _query_first_funders(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """First in-transfer per holder — funder, evidence tx.

    EVM:  ``evm_transfers`` in-rows for the token.
    Solana: native-SOL funders from ``solana_transfers`` (trace table) for
    holders of the mint (owners with positive deltas in
    ``solana_spl_balance_changes``). Graceful: when the trace table is
    empty for untracked mints, funder rows are absent and the leg degrades
    to an empty list (fail-open).
    """
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        o.owner AS holder,
                        argMin(f.from_address, f.block_timestamp) AS funder,
                        argMin(f.signature, f.block_timestamp) AS first_tx
                    FROM (
                        SELECT DISTINCT owner
                        FROM wallet_memory.solana_spl_balance_changes
                        WHERE mint = %(token)s
                          AND toFloat64OrZero(delta) > 0
                          AND owner != ''
                    ) o
                    LEFT JOIN (
                        SELECT from_address, to_address, block_timestamp, signature
                        FROM wallet_memory.solana_transfers
                        WHERE transfer_type IN ('native_sol', 'cpi_sol')
                          AND from_address != ''
                          AND to_address != ''
                    ) f ON f.to_address = o.owner
                    GROUP BY o.owner
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "limit": _MAX_HOLDERS},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_first_funders_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                to_address AS holder,
                argMin(from_address, block_timestamp) AS funder,
                argMin(tx_hash, block_timestamp) AS first_tx
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
            GROUP BY to_address
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "limit": _MAX_HOLDERS},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_first_funders_query_failed: %s", exc)
        return None


def _query_deployer_token_overlap(
    deployer: str,
    holders: list[str],
    token_address: str,
    chain: str,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Other tokens by the same deployer + which of our holders co-hold them."""
    if not deployer or not holders:
        return None
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        t.mint AS token_address,
                        t.creator AS deployer,
                        arrayFilter(
                            h -> h IN (SELECT DISTINCT owner
                                       FROM wallet_memory.solana_spl_balance_changes
                                       WHERE mint = t.mint AND owner != ''),
                            %(holders)s
                        ) AS shared_holders
                    FROM (
                        SELECT mint, argMin(creator, block_timestamp) AS creator
                        FROM wallet_memory.solana_launches
                        WHERE creator = %(deployer)s
                          AND mint != %(token)s
                        GROUP BY mint
                        ORDER BY min(block_timestamp) DESC
                        LIMIT %(limit)s
                    ) t
                    WHERE length(shared_holders) >= 1
                    """,
                    {
                        "deployer": deployer,
                        "token": token_address,
                        "holders": holders,
                        "limit": limit,
                    },
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_shared_deployer_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                t.token_address,
                t.deployer,
                arrayFilter(
                    h -> h IN (SELECT to_address FROM wallet_memory.evm_transfers
                               WHERE token_address = t.token_address AND chain = %(chain)s),
                    %(holders)s
                ) AS shared_holders
            FROM (
                SELECT token_address, argMin(deployer, block_timestamp) AS deployer
                FROM wallet_memory.deployer_events
                WHERE chain = %(chain)s
                  AND deployer = %(deployer)s
                  AND token_address != %(token)s
                GROUP BY token_address
                ORDER BY min(block_timestamp) DESC
                LIMIT %(limit)s
            ) t
            WHERE length(shared_holders) >= 1
            """,
            {
                "deployer": deployer,
                "token": token_address,
                "chain": chain,
                "holders": holders,
                "limit": limit,
            },
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_shared_deployer_query_failed: %s", exc)
        return None


def _query_cross_token_overlap(
    holders: list[str],
    token_address: str,
    chain: str,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Other tokens where >= 2 of our holders co-hold (cross-token clusters)."""
    if not holders:
        return None
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        mint AS token_address,
                        groupArray(owner) AS co_holders
                    FROM (
                        SELECT DISTINCT mint, owner
                        FROM wallet_memory.solana_spl_balance_changes
                        WHERE mint != %(token)s
                          AND owner IN %(holders)s
                          AND owner != ''
                    )
                    GROUP BY mint
                    HAVING length(co_holders) >= 2
                    ORDER BY length(co_holders) DESC
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "holders": holders, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_cross_token_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                token_address,
                groupArray(to_address) AS co_holders
            FROM (
                SELECT DISTINCT token_address, to_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND token_address != %(token)s
                  AND to_address IN %(holders)s
            )
            GROUP BY token_address
            HAVING length(co_holders) >= 2
            ORDER BY length(co_holders) DESC
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "holders": holders, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_cross_token_query_failed: %s", exc)
        return None


def _query_cross_token_funders(
    holders: list[str],
    token_address: str,
    chain: str,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Funders of our holders that also funded wallets in OTHER launches.

    Returns rows ``(funder, other_token, [funded_wallets...])`` — the raw
    edges of the cross-token funder graph (leg 4). Solana uses
    ``solana_transfers`` native-SOL edges to owners of other mints.
    """
    if not holders:
        return None
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        f.from_address AS funder,
                        b.mint AS other_token,
                        groupArray(DISTINCT f.to_address) AS funded_wallets
                    FROM wallet_memory.solana_transfers f
                    JOIN wallet_memory.solana_spl_balance_changes b
                      ON b.owner = f.to_address AND b.mint != %(token)s
                    WHERE f.transfer_type IN ('native_sol', 'cpi_sol')
                      AND f.from_address != ''
                      AND f.to_address != ''
                      AND f.from_address IN (
                          SELECT DISTINCT from_address
                          FROM wallet_memory.solana_transfers
                          WHERE transfer_type IN ('native_sol', 'cpi_sol')
                            AND from_address != ''
                            AND to_address != ''
                      )
                    GROUP BY f.from_address, b.mint
                    ORDER BY length(funded_wallets) DESC
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_cross_token_funders_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                from_address AS funder,
                token_address AS other_token,
                groupArray(to_address) AS funded_wallets
            FROM (
                SELECT DISTINCT from_address, to_address, token_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND token_address != %(token)s
                  AND from_address IN (
                      SELECT DISTINCT from_address
                      FROM wallet_memory.evm_transfers
                      WHERE token_address = %(token)s AND chain = %(chain)s
                  )
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                              '0x000000000000000000000000000000000000dead')
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
            )
            GROUP BY funder, other_token
            ORDER BY length(funded_wallets) DESC
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "holders": holders, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_cross_token_funders_query_failed: %s", exc)
        return None


def _query_first_buy_blocks(token_address: str, chain: str, limit: int) -> list[tuple[Any, ...]] | None:
    """First buy per holder (buyer, first_block, first_tx) — temporal leg.

    EVM: first DEX swap per holder from ``dex_events``. Solana: first
    positive balance delta per owner from ``solana_spl_balance_changes``
    (slot serves as the block anchor; tx_hash as evidence).
    """
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        if(owner != '', owner, token_account) AS buyer,
                        min(slot) AS first_block,
                        argMin(tx_hash, block_timestamp) AS first_tx
                    FROM wallet_memory.solana_spl_balance_changes
                    WHERE mint = %(token)s
                      AND toFloat64OrZero(delta) > 0
                      AND owner != ''
                    GROUP BY buyer
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_first_buy_blocks_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                buyer,
                min(block_number) AS first_block,
                argMin(tx_hash, block_timestamp) AS first_tx
            FROM (
                SELECT
                    if(recipient NOT IN ('', '0x0000000000000000000000000000000000000000'),
                       recipient, sender) AS buyer,
                    block_number,
                    block_timestamp,
                    tx_hash
                FROM wallet_memory.dex_events
                WHERE chain = %(chain)s
                  AND (token0 = %(token)s OR token1 = %(token)s)
                  AND type = 'swap'
                  AND sender NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
            )
            WHERE buyer != ''
            GROUP BY buyer
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_first_buy_blocks_query_failed: %s", exc)
        return None


def _query_deploy_info(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """Deploy anchor: (deploy_block_number, min(block_timestamp)).

    EVM reads ``deployer_events``; Solana reads ``solana_launches``
    (slot = block number).
    """
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            rows = client.execute(
                """
                SELECT
                    argMin(slot, block_timestamp) AS deploy_block,
                    min(block_timestamp) AS deploy_ts
                FROM wallet_memory.solana_launches
                WHERE mint = %(token)s
                """,
                {"token": token_address},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_deploy_info_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                argMin(block_number, block_timestamp) AS deploy_block,
                min(block_timestamp) AS deploy_ts
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s AND chain = %(chain)s
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_deploy_info_query_failed: %s", exc)
        return None


def _query_earliest_transfer(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """Fallback launch anchor: earliest transfer (block_number, block_timestamp).

    EVM reads ``evm_transfers``; Solana reads ``solana_spl_balance_changes``
    (min slot + min block_timestamp).
    """
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            rows = client.execute(
                """
                SELECT
                    min(slot) AS first_block,
                    min(block_timestamp) AS first_ts
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                """,
                {"token": token_address},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_earliest_transfer_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                min(block_number) AS first_block,
                min(block_timestamp) AS first_ts
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s AND chain = %(chain)s
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_earliest_transfer_query_failed: %s", exc)
        return None


def _query_launch_window_transfers(
    token_address: str,
    chain: str,
    anchor_block: int,
    window_blocks: int,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Launch-window buy rows: (wallet, amount, block, ts, tx_hash).

    EVM reads ``evm_transfers``; Solana reads ``solana_spl_balance_changes``
    (slot = block, delta = amount).
    """
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        if(owner != '', owner, token_account) AS wallet,
                        toFloat64OrZero(delta) AS amount,
                        slot AS block_number,
                        block_timestamp,
                        tx_hash
                    FROM wallet_memory.solana_spl_balance_changes
                    WHERE mint = %(token)s
                      AND slot >= %(anchor)s
                      AND slot <= %(anchor)s + %(window)s
                      AND toFloat64OrZero(delta) > 0
                      AND owner != ''
                    ORDER BY slot, block_timestamp
                    LIMIT %(limit)s
                    """,
                    {
                        "token": token_address,
                        "anchor": int(anchor_block),
                        "window": int(window_blocks),
                        "limit": limit,
                    },
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_launch_window_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                to_address AS wallet,
                toFloat64OrZero(value) AS amount,
                block_number,
                block_timestamp,
                tx_hash
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND block_number >= %(anchor)s
              AND block_number <= %(anchor)s + %(window)s
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
            ORDER BY block_number, block_timestamp
            LIMIT %(limit)s
            """,
            {
                "token": token_address,
                "chain": chain,
                "anchor": int(anchor_block),
                "window": int(window_blocks),
                "limit": limit,
            },
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_launch_window_query_failed: %s", exc)
        return None


def _query_wallets_with_prior_tx(
    wallets: list[str],
    chain: str,
    cutoff_ts: float | None,
) -> list[tuple[Any, ...]] | None:
    """Wallets that appear in ANY transfer before the launch anchor (prior history).

    EVM uses ``evm_transfers``; Solana uses ``solana_spl_balance_changes``
    (owner has balance-change rows before the cutoff).
    """
    if not wallets:
        return None
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            if cutoff_ts is None:
                rows = client.execute(
                    """
                    SELECT DISTINCT owner
                    FROM wallet_memory.solana_spl_balance_changes
                    WHERE owner IN %(wallets)s
                    LIMIT %(limit)s
                    """,
                    {"wallets": wallets, "limit": len(wallets)},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                )
            else:
                rows = client.execute(
                    """
                    SELECT DISTINCT owner
                    FROM wallet_memory.solana_spl_balance_changes
                    WHERE owner IN %(wallets)s
                      AND block_timestamp < toDateTime(%(cutoff)s)
                    LIMIT %(limit)s
                    """,
                    {"wallets": wallets, "cutoff": float(cutoff_ts), "limit": len(wallets)},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_prior_tx_solana_query_failed: %s", exc)
            return None
    try:
        if cutoff_ts is None:
            rows = client.execute(
                """
                SELECT DISTINCT to_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND to_address IN %(wallets)s
                LIMIT %(limit)s
                """,
                {"chain": chain, "wallets": wallets, "limit": len(wallets)},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
        else:
            rows = client.execute(
                """
                SELECT DISTINCT to_address
                FROM wallet_memory.evm_transfers
                WHERE chain = %(chain)s
                  AND to_address IN %(wallets)s
                  AND block_timestamp < toDateTime(%(cutoff)s)
                LIMIT %(limit)s
                """,
                {"chain": chain, "wallets": wallets, "cutoff": float(cutoff_ts), "limit": len(wallets)},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_prior_tx_query_failed: %s", exc)
        return None


def _query_token_transfers(
    token_address: str,
    chain: str,
    lookback_hours: int,
    limit: int,
) -> list[tuple[Any, ...]] | None:
    """Recent token transfers (from, to, amount, ts) for wash-cycle detection.

    EVM reads ``evm_transfers``; Solana reads ``solana_transfers`` trace
    rows (native + SPL edges between real wallets — the wash-cycle DFS needs
    concrete from/to addresses, which ``solana_spl_balance_changes`` does
    not carry).
    """
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            return cast(
                "list[tuple[Any, ...]] | None",
                client.execute(
                    """
                    SELECT
                        from_address,
                        to_address,
                        toFloat64OrZero(amount) AS amount,
                        block_timestamp
                    FROM wallet_memory.solana_transfers
                    WHERE mint = %(token)s
                      AND block_timestamp >= now() - INTERVAL %(lookback)s HOUR
                      AND from_address != ''
                      AND to_address != ''
                    ORDER BY block_timestamp
                    LIMIT %(limit)s
                    """,
                    {"token": token_address, "lookback": int(lookback_hours), "limit": limit},
                    settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
                ),
            )
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_wash_transfers_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT
                from_address,
                to_address,
                toFloat64OrZero(value) AS amount,
                block_timestamp
            FROM wallet_memory.evm_transfers
            WHERE token_address = %(token)s
              AND chain = %(chain)s
              AND block_timestamp >= now() - INTERVAL %(lookback)s HOUR
              AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                          '0x000000000000000000000000000000000000dead')
              AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000',
                                        '0x000000000000000000000000000000000000dead')
            ORDER BY block_timestamp
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "lookback": int(lookback_hours), "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_wash_transfers_query_failed: %s", exc)
        return None


async def _resolve_launch_anchor(token_address: str, chain_norm: str) -> tuple[int | None, float | None]:
    """Resolve (anchor_block, anchor_ts) from deployer_events, else earliest transfer.

    ``anchor_ts`` is an epoch float (may be None when the timestamp column is
    unavailable); it only feeds the optional first-tx-ever heuristic, so a
    missing value degrades gracefully.
    """
    try:
        rows = await run_sync(_query_deploy_info, token_address, chain_norm)
        if rows and rows[0] and rows[0][0]:
            block = int(rows[0][0])
            ts = to_epoch(rows[0][1])
            return block, ts
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError):
        pass
    try:
        rows = await run_sync(_query_earliest_transfer, token_address, chain_norm)
        if rows and rows[0] and rows[0][0]:
            return int(rows[0][0]), to_epoch(rows[0][1])
    except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError):
        pass
    return None, None


async def _resolve_inputs(
    token_address: str,
    chain_norm: str,
    *,
    deployer: str | None,
    holders: list[str] | None,
) -> dict[str, Any]:
    """Resolve deployer + top holders from own data when not provided."""
    resolved: dict[str, Any] = {"deployer": deployer, "holders": holders or []}

    if not deployer:
        try:
            rows = await run_sync(_query_deployer, token_address, chain_norm)
            if rows:
                resolved["deployer"] = str(rows[0] or "").lower() or None
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError):
            resolved["deployer"] = None

    if not resolved.get("holders"):
        try:
            rows = await run_sync(_query_top_holders, token_address, chain_norm, _MAX_HOLDERS)
            resolved["holders"] = [str(r[0] or "").lower() for r in (rows or []) if r and r[0]]
        except (ConnectionError, OSError, TimeoutError, ValueError, KeyError, TypeError, CHDriverError):
            resolved["holders"] = []

    return resolved


def _query_deployer(token_address: str, chain: str) -> list[tuple[Any, ...]] | None:
    """Earliest deployer for the token — EVM deployer_events / Solana solana_launches."""
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            rows = client.execute(
                """
                SELECT argMin(creator, block_timestamp)
                FROM wallet_memory.solana_launches
                WHERE mint = %(token)s
                  AND launch_signal = 1
                """,
                {"token": token_address},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_deployer_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT argMin(deployer, block_timestamp)
            FROM wallet_memory.deployer_events
            WHERE token_address = %(token)s AND chain = %(chain)s
            """,
            {"token": token_address, "chain": chain},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_deployer_query_failed: %s", exc)
        return None


def _query_top_holders(token_address: str, chain: str, limit: int) -> list[tuple[Any, ...]] | None:
    """Top holders by net balance — EVM evm_transfers / Solana balance deltas."""
    client = _get_ch_client()
    if client is None:
        return None
    if chain == "solana":
        try:
            rows = client.execute(
                """
                SELECT
                    if(owner != '', owner, token_account) AS holder,
                    sum(toFloat64OrZero(delta)) AS bal
                FROM wallet_memory.solana_spl_balance_changes
                WHERE mint = %(token)s
                GROUP BY holder
                HAVING bal > 0
                ORDER BY bal DESC
                LIMIT %(limit)s
                """,
                {"token": token_address, "limit": limit},
                settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
            )
            return cast("list[tuple[Any, ...]] | None", rows)
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug("risk_cluster_top_holders_solana_query_failed: %s", exc)
            return None
    try:
        rows = client.execute(
            """
            SELECT addr
            FROM (
                SELECT to_address AS addr, sum(toFloat64OrZero(value)) AS bal
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s AND chain = %(chain)s
                  AND to_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                GROUP BY addr
                UNION ALL
                SELECT from_address AS addr, -sum(toFloat64OrZero(value)) AS bal
                FROM wallet_memory.evm_transfers
                WHERE token_address = %(token)s AND chain = %(chain)s
                  AND from_address NOT IN ('', '0x0000000000000000000000000000000000000000')
                GROUP BY addr
            )
            WHERE addr != ''
            GROUP BY addr
            HAVING sum(bal) > 0
            ORDER BY sum(bal) DESC
            LIMIT %(limit)s
            """,
            {"token": token_address, "chain": chain, "limit": limit},
            settings={"max_execution_time": _QUERY_TIMEOUT_SECONDS},
        )
        return cast("list[tuple[Any, ...]] | None", rows)
    except (ValueError, RuntimeError, OSError) as exc:
        logger.debug("risk_cluster_top_holders_query_failed: %s", exc)
        return None


def _unavailable_result(
    token_address: str,
    chain_norm: str,
    reason: str,
    *,
    deployer: str | None = None,
    holders_analyzed: int = 0,
) -> dict[str, Any]:
    """Graceful degradation result (never raises / never blocks the scan)."""
    return {
        "check": "risk_cluster",
        "result": "unavailable",
        "source": "own",
        "token_address": token_address,
        "chain": chain_norm,
        "deployer": deployer,
        "holders_analyzed": holders_analyzed,
        "funder_tree": [],
        "shared_deployer_network": [],
        "cross_token_clusters": [],
        "cross_token_funders": [],
        "temporal_clusters": [],
        "sniper_patterns": [],
        "mev_bundles": [],
        "wash_trading": {"cycles": [], "self_swaps": [], "cycle_volume_ratio": 0.0, "wash_score": 0},
        "cluster_risk_score": 0,
        "cluster_risk_label": "LOW",
        "cluster_risk_description": f"Cluster data unavailable: {reason}",
        "component_scores": {},
        "note": reason,
    }
