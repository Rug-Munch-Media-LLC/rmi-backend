"""Pure graph builders for the risk_cluster package (no database).

These take plain dicts / tuples so they are unit-testable without a database.
They build the funder tree, shared-deployer networks, cross-token cluster
graph, cross-token funder graph and temporal (lockstep) clusters.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines.risk_cluster._constants import (
    _DEAD_ADDR,
    _LAUNCH_WINDOW_BLOCKS,
    _MIN_CROSS_TOKEN_FUNDER_TOKENS,
    _MIN_CROSS_TOKEN_MEMBERS,
    _MIN_FUNDER_CHILDREN,
    _MIN_SHARED_DEPLOYER_MEMBERS,
    _MIN_TEMPORAL_MEMBERS,
    _TEMPORAL_BLOCK_WINDOW,
    _ZERO_ADDR,
)


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
