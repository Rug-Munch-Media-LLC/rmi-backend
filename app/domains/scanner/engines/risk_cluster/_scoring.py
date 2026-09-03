"""Composite cluster-risk scoring for the risk_cluster package.

``score_cluster_risk`` combines the eight cluster structures into a single
0-100 risk score with a per-component breakdown and a LOW/MEDIUM/HIGH/CRITICAL
label.
"""

from __future__ import annotations

from typing import Any


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
    risk_int = round(score)

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
