"""Launch-window graph builders for the risk_cluster package (no database).

Sniper/bot pattern detection, MEV-bundle detection and wash-trading cluster
detection. All take plain dicts / tuples so they are unit-testable without a
database.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.engines.risk_cluster._constants import (
    _BOT_CV_THRESHOLD,
    _DEAD_ADDR,
    _LAUNCH_WINDOW_BLOCKS,
    _MAX_WASH_CYCLE_LEN,
    _MEV_AMOUNT_BAND,
    _MEV_TIP_BAND_PCT,
    _MIN_WASH_CYCLE_LEN,
    _WASH_LOOKBACK_HOURS,
    _ZERO_ADDR,
)


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
    _signal_order = {
        "sniper_wave": 0,
        "same_block_snipe": 1,
        "fresh_wallet_bots": 2,
        "uniform_bot_amounts": 3,
        "tip_cluster": 4,
    }
    return sorted(
        patterns,
        key=lambda p: (_signal_order.get(str(p.get("pattern") or ""), 9), -int(p.get("members") or 0)),
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

    _mev_order = {"identical_tip_bundle": 0, "atomic_bundle_buy": 1, "buy_then_distribute": 2}
    return sorted(
        bundles,
        key=lambda b: (_mev_order.get(str(b.get("pattern") or ""), 9), -int(b.get("members") or 0)),
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
            _walk(start, [*path, nxt], visited)
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
        "wash_score": round(wash_score),
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
