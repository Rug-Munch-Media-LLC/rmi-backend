"""Normalized cluster/bundle block for rugmaps scan responses.

Pure derivation from a ``BundleReport`` produced by the ONE bundle engine we
reuse everywhere (``BundleDetector`` — same engine as /early-buyers and
/bundle-timeline). No I/O, no detection logic, no identity claims: a cluster is
a behavioural grouping, never proof of common control. Non-derivable fields are
``None`` with a stated ``null_reasons`` entry.
"""

from __future__ import annotations

from typing import Any

SOURCE = "bundle_detector"

_NOTE = (
    "A cluster is a behavioural grouping of on-chain addresses derived from "
    "funding paths and buy timing. It is not evidence of common control, "
    "shared identity, or intent. No entity or person is attributed."
)


def _null_block(reason: str) -> dict[str, Any]:
    """Fully-null block stating why nothing could be derived."""
    return {
        "available": False,
        "cluster_count": None,
        "clusters": [],
        "largest_cluster": None,
        "bundled_supply_pct": None,
        "same_block_sniper_count": None,
        "shared_funder": None,
        "fresh_wallet_share_pct": None,
        "confidence": None,
        "confidence_basis": "unavailable",
        "evidence": [],
        "source": SOURCE,
        "null_reasons": dict.fromkeys(
            (
                "cluster_count",
                "bundled_supply_pct",
                "same_block_sniper_count",
                "shared_funder",
                "fresh_wallet_share_pct",
                "confidence",
            ),
            reason,
        ),
        "assessment": {"coordinated": None, "basis": "unavailable", "identity_claims": [], "note": _NOTE},
    }


def _origin_funder(wallet: Any) -> tuple[str, str]:
    """Deepest-origin funder of a bundled wallet: earliest funding step.

    Timestamp ordering is used rather than list position because the funding
    chain direction must not be guessed.
    """
    steps = [s for s in (getattr(wallet, "funding_chain", None) or []) if getattr(s, "address", "")]
    if not steps:
        return "", ""
    origin = min(steps, key=lambda s: getattr(s, "timestamp", 0) or 0)
    return str(getattr(origin, "address", "")), str(getattr(origin, "tx_signature", "") or "")


def build_bundle_block(report: Any | None, *, fresh_share_pct: float | None = None) -> dict[str, Any]:
    """Normalize a BundleReport into the scan-response ``clusters`` block.

    ``cluster_count`` adds behaviour clusters plus NON-overlapping funder groups
    (no double counting). ``confidence`` is the strongest single signal, not a
    sum — correlated heuristics would overstate certainty.
    """
    if report is None:
        return _null_block("bundle_report_unavailable")

    null_reasons: dict[str, str] = {}
    evidence: list[dict[str, Any]] = []
    clusters: list[dict[str, Any]] = []
    covered: set[str] = set()

    deployer = str(getattr(report, "deployer", "") or "")
    deploy_block = int(getattr(report, "deploy_block", 0) or 0)
    if deployer and deployer != "unknown":
        evidence.append({"kind": "deployer", "address": deployer, "deploy_block": deploy_block or None})

    max_conf = 0.0
    for i, bc in enumerate(getattr(report, "behavior_clusters", None) or []):
        wallets = [str(w) for w in (getattr(bc, "wallets", None) or [])]
        if not wallets:
            continue
        conf = float(getattr(bc, "confidence", 0) or 0)
        max_conf = max(max_conf, conf)
        covered.update(wallets)
        clusters.append({
            "cluster_id": f"behaviour-{i + 1}",
            "basis": "behaviour_pattern",
            "pattern": str(getattr(bc, "pattern", "") or "unknown"),
            "wallet_count": len(wallets),
            "wallets": wallets[:20],
            "combined_supply_pct": getattr(bc, "combined_percentage", None),
            "confidence": round(conf, 3),
        })
        evidence.append({
            "kind": "behaviour_cluster", "pattern": clusters[-1]["pattern"],
            "wallet_count": len(wallets), "wallets": wallets[:10],
            "combined_supply_pct": getattr(bc, "combined_percentage", None), "confidence": round(conf, 3),
        })

    groups: dict[str, dict[str, Any]] = {}
    for w in getattr(report, "deployer_funded", None) or []:
        addr = str(getattr(w, "address", "") or "")
        funder, tx = _origin_funder(w)
        if not addr or not funder:
            continue
        g = groups.setdefault(funder, {"wallets": [], "tx": tx})
        g["wallets"].append(addr)

    shared_funder = None
    for funder, g in sorted(groups.items(), key=lambda kv: -len(kv[1]["wallets"])):
        if len(g["wallets"]) < 2:
            continue
        overlap = len(set(g["wallets"]) & covered)
        if overlap < len(g["wallets"]) / 2:
            clusters.append({
                "cluster_id": f"funder-{funder[:8]}",
                "basis": "shared_funder",
                "pattern": "shared_funding_origin",
                "wallet_count": len(g["wallets"]),
                "wallets": g["wallets"][:20],
                "combined_supply_pct": None,
                "confidence": 0.9,
            })
            evidence.append({
                "kind": "shared_funder", "funder": funder, "wallet_count": len(g["wallets"]),
                "wallets": g["wallets"][:10], "funding_tx": g["tx"] or None,
            })
            max_conf = max(max_conf, 0.9)
        if shared_funder is None:
            shared_funder = {
                "address": funder,
                "wallet_count": len(g["wallets"]),
                "wallets": g["wallets"][:20],
                "funding_tx": g["tx"] or None,
            }

    snipers = [s for s in (getattr(report, "same_block_snipers", None) or []) if getattr(s, "address", "")]
    sniper_count = len(snipers)
    if sniper_count:
        max_conf = max(max_conf, 0.8)
        block_no = int(getattr(snipers[0], "block_number", 0) or 0)
        evidence.append({
            "kind": "same_block_snipers",
            "block": block_no or None,
            "wallet_count": sniper_count,
            "wallets": [str(getattr(s, "address", "")) for s in snipers[:10]],
        })

    bundled_pct = getattr(report, "total_bundled_percentage", None)
    bundled_pct = round(float(bundled_pct), 2) if bundled_pct not in (None, 0) else None

    if not clusters:
        null_reasons["cluster_count"] = "no_behaviour_or_funder_group_with_2plus_wallets"
    if bundled_pct is None:
        null_reasons["bundled_supply_pct"] = "engine_reported_no_derivable_supply_share"
    if sniper_count == 0:
        null_reasons["same_block_sniper_count"] = "no_same_block_buys_detected"
    if shared_funder is None:
        null_reasons["shared_funder"] = "no_funding_origin_shared_by_2plus_wallets"
    if fresh_share_pct is None:
        null_reasons["fresh_wallet_share_pct"] = "no_wallet_age_data_for_clustered_addresses"

    coordinated = sniper_count >= 3 or (shared_funder is not None and shared_funder["wallet_count"] >= 3)
    if coordinated:
        basis = "same_block_snipers>=3" if sniper_count >= 3 else "shared_funder_group>=3_wallets"
    else:
        basis = "insufficient_evidence"

    largest = max(clusters, key=lambda c: c["wallet_count"], default=None)
    return {
        "available": True,
        "cluster_count": len(clusters),
        "clusters": clusters,
        "largest_cluster": largest,
        "bundled_supply_pct": bundled_pct,
        "same_block_sniper_count": sniper_count,
        "shared_funder": shared_funder,
        "fresh_wallet_share_pct": round(float(fresh_share_pct), 2) if fresh_share_pct is not None else None,
        "confidence": round(min(1.0, max_conf), 3) if clusters or sniper_count else None,
        "confidence_basis": "max_single_evidence_signal",
        "evidence": evidence,
        "source": SOURCE,
        "null_reasons": null_reasons,
        "assessment": {"coordinated": coordinated, "basis": basis, "identity_claims": [], "note": _NOTE},
    }


__all__ = ["build_bundle_block"]
