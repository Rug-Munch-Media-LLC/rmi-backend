"""Factor-construction helpers + per-section extraction for the risk_report package.

Defines the small coercion helpers (``_f`` / ``_i`` / ``_sec`` /
``_sentinel_module`` / ``_holders_dict``), the ``_factor`` builder, and the
per-section extractors (deployer / holders / first-buyer / bundle / honeypot /
liquidity) that pull data out of a ``ScanResult`` defensively.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.unified.risk_report._model import (
    _CRITICAL_MIN,
    _RISK_MIN,
    FACTOR_LABELS,
    RiskFactor,
)

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)

def _factor(key: str, score: float | None, explanation: str) -> RiskFactor:
    """Build a RiskFactor from a score + why-it's-risky text (band mapping)."""
    if score is None:
        return RiskFactor(
            key=key,
            label=FACTOR_LABELS.get(key, key),
            risk_score=None,
            status="unknown",
            explanation=explanation,
        )
    status = "critical" if score >= _CRITICAL_MIN else ("risk" if score >= _RISK_MIN else "ok")
    return RiskFactor(
        key=key,
        label=FACTOR_LABELS.get(key, key),
        risk_score=round(score, 1),
        status=status,
        explanation=explanation,
    )


def _f(value: Any) -> float | None:
    """Coerce to float, returning None for missing/NaN values."""
    try:
        if value is None:
            return None
        v = float(value)
        if v != v:  # NaN
            return None
        return v
    except (TypeError, ValueError):
        return None


def _i(value: Any) -> int:
    """Coerce to int (0 when missing/unparseable)."""
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _sec(r: ScanResult) -> dict[str, Any]:
    return r.security if isinstance(r.security, dict) else {}


def _sentinel_module(r: ScanResult, key: str) -> dict[str, Any]:
    """Pull one SENTINEL module result (free or premium bucket) defensively."""
    sent = r.sentinel if isinstance(r.sentinel, dict) else {}
    for bucket in ("free", "premium"):
        mods = sent.get(bucket)
        if isinstance(mods, dict):
            res = mods.get(key)
            if isinstance(res, dict):
                return res
    return {}


def _holders_dict(r: ScanResult) -> dict[str, Any]:
    return r.holders if isinstance(r.holders, dict) else {}


# ═══════════════════════════════════════════════════════════════════════════
# SECTION EXTRACTION
# ═══════════════════════════════════════════════════════════════════════════


def _extract_deployer_section(
    r: ScanResult,
    fingerprint: dict[str, Any] | None,
    holder_clusters: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """Assemble the deployer section + resolved deployer address (or None)."""
    deployer: str | None = None

    # 1. Explicit fingerprint payload (own engine) wins.
    if isinstance(fingerprint, dict) and fingerprint.get("deployer"):
        deployer = str(fingerprint["deployer"]).lower()

    # 2. holder_clusters exposes the deployer it resolved from deployer_events.
    if deployer is None and isinstance(holder_clusters, dict) and holder_clusters.get("deployer"):
        deployer = str(holder_clusters["deployer"]).lower()

    # 3. Fall back to security payload creator keys.
    if deployer is None:
        for key in ("creator_address", "creator", "deployer", "deployer_address", "owner_address"):
            value = _sec(r).get(key)
            if isinstance(value, str) and len(value) >= 32 and value not in ("", "unknown", "null"):
                deployer = value.lower()
                break

    # 4. SENTINEL free contract_authority / dev_reputation may carry it.
    if deployer is None:
        ca = _sentinel_module(r, "contract_authority")
        for key in ("owner_address", "deployer", "mint_authority_address"):
            value = ca.get(key)
            if isinstance(value, str) and len(value) >= 32 and value not in ("", "unknown", "null"):
                deployer = value.lower()
                break

    section: dict[str, Any] = {"address": deployer, "repeat_deployer": None, "previously_rugged": None}

    if isinstance(fingerprint, dict) and fingerprint.get("result") == "scanned":
        bucket = fingerprint.get("risk_bucket")
        total = _i(fingerprint.get("total_deployments"))
        success = _f(fingerprint.get("success_rate"))
        funder = fingerprint.get("funder_profile") or {}
        section.update(
            {
                "risk_bucket": bucket,
                "risk_score": _f(fingerprint.get("risk_score")),
                "total_deployments": total,
                "success_rate": success,
                "median_lifespan_hours": _f(fingerprint.get("median_lifespan_hours")),
                "chains_used": fingerprint.get("chains_used") or [],
                "first_seen": fingerprint.get("first_seen"),
                "repeat_deployer": total > 1,
                "previously_rugged": bucket in ("REPEAT_RUG", "FARM") or (success is not None and success < 0.5),
                "funder": {
                    "address": funder.get("funder_address"),
                    "is_cex": bool(funder.get("funder_is_cex")),
                    "cex_label": funder.get("cex_label"),
                    "is_fresh": funder.get("funder_is_fresh"),
                    "same_funder_multiple": funder.get("same_funder_multiple"),
                },
                "history": (fingerprint.get("evidence") or [])[:10],
            }
        )
    elif isinstance(r.dev_reputation, dict):
        dev = r.dev_reputation
        rug_rate = _f(dev.get("rug_rate"))
        section.update(
            {
                "risk_score": _f(dev.get("risk_score")),
                "total_deployments": _i(dev.get("total_deployments", dev.get("total_launches"))),
                "repeat_deployer": _i(dev.get("total_deployments", dev.get("total_launches"))) > 1,
                "previously_rugged": rug_rate >= 0.5 if rug_rate is not None else None,
            }
        )

    return section, deployer


def _extract_holders_section(
    r: ScanResult,
    holder_clusters: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], str | None]:
    """Assemble the holders section + top-25 holder list + resolved deployer.

    Preferred order: holder_clusters (BubbleMaps-style own data with labels) →
    DataBus holder_data → SENTINEL holder_analysis.
    """
    deployer = None
    top_25: list[dict[str, Any]] = []
    holders_out: dict[str, Any] = {
        "total_holders": None,
        "top_10_pct": None,
        "top_25_pct": None,
        "concentration": "unknown",
        "gini": None,
        "hhi": None,
        "dev_team_share": None,
        "source": "unknown",
    }

    if isinstance(holder_clusters, dict) and holder_clusters.get("result") == "scanned":
        deployer = holder_clusters.get("deployer")
        metrics = holder_clusters.get("metrics") or {}
        conc = holder_clusters.get("concentration") or {}
        holders = holder_clusters.get("holders") or []
        if isinstance(holders, list):
            for h in holders[:25]:
                if not isinstance(h, dict):
                    continue
                top_25.append(
                    {
                        "address": h.get("address"),
                        "share_pct": h.get("balance_pct"),
                        "label": h.get("label"),
                        "cluster_id": h.get("cluster_id"),
                        "first_seen": h.get("first_seen"),
                    }
                )
        holders_out.update(
            {
                "total_holders": metrics.get("total_holders"),
                "top_10_pct": conc.get("top10_pct"),
                "top_25_pct": None,  # own engine reports top-10; top-25 computed below when possible
                "concentration": conc.get("level", "unknown"),
                "gini": metrics.get("gini"),
                "hhi": metrics.get("hhi"),
                "dev_team_share": _dev_team_share(top_25, deployer),
                "source": "own_holder_clusters",
                "clusters_found": len(holder_clusters.get("clusters") or []),
            }
        )
        # Top-25 share: sum of top-25 balances when at least 10 holders listed.
        pcts: list[float] = []
        for h in top_25:
            p = h.get("share_pct")
            if isinstance(p, (int, float)):
                pcts.append(float(p))
        if len(pcts) >= 10:
            holders_out["top_25_pct"] = round(min(sum(pcts[:25]), 100.0), 1)

    if not top_25:
        hd = _holders_dict(r)
        raw_top = hd.get("top_holders") or []
        if isinstance(raw_top, list):
            for idx, h in enumerate(raw_top[:25], start=1):
                if not isinstance(h, dict):
                    continue
                top_25.append(
                    {
                        "address": h.get("address"),
                        "share_pct": h.get("percentage", h.get("share_pct")),
                        "label": h.get("label"),
                        "cluster_id": None,
                        "first_seen": None,
                        "rank": idx,
                    }
                )
        elif isinstance(raw_top, dict):
            for addr, pct in list(raw_top.items())[:25]:
                top_25.append(
                    {"address": addr, "share_pct": pct, "label": None, "cluster_id": None, "first_seen": None}
                )
        holders_out.update(
            {
                "total_holders": hd.get("holder_count", hd.get("total_holders")),
                "top_10_pct": hd.get("top_10_pct", hd.get("top10_pct")),
                "top_25_pct": hd.get("top_25_pct"),
                "concentration": "unknown",
                "gini": hd.get("gini_coefficient", hd.get("gini")),
                "hhi": None,
                "dev_team_share": None,
                "source": "databus_holder_data" if hd else "none",
            }
        )

    if not top_25:
        ha = _sentinel_module(r, "holder_analysis")
        if isinstance(ha, dict):
            holders_out["total_holders"] = ha.get("total_holders")
            holders_out["top_10_pct"] = ha.get("top10_percentage", ha.get("top_10_percentage"))
            holders_out["gini"] = ha.get("gini")
            holders_out["hhi"] = ha.get("hhi")
            holders_out["source"] = "sentinel_holder_analysis"

    return holders_out, top_25, deployer


def _dev_team_share(top_25: list[dict[str, Any]], deployer: str | None) -> float | None:
    """Sum of the top-25 share held by the deployer + team-labelled wallets."""
    deployer_l = (deployer or "").lower()
    share = 0.0
    counted = False
    for h in top_25:
        addr = str(h.get("address") or "").lower()
        pct = h.get("share_pct")
        label = str(h.get("label") or "").lower()
        is_team = bool(addr and deployer_l and addr == deployer_l) or label in ("team", "deployer")
        if not is_team:
            continue
        try:
            p = float(pct) if pct is not None else 0.0
        except (TypeError, ValueError):
            p = 0.0
        share += p
        counted = True
    return round(share, 1) if counted else None


def _extract_first_buyer_section(
    r: ScanResult,
    deployer: str | None,
    bundle: dict[str, Any] | None,
) -> dict[str, Any]:
    """First buyer from own first-buyers cohort (250-wallet deep engine) or
    own bundle-launch data (first launch buyer)."""
    section: dict[str, Any] = {
        "address": None,
        "is_deployer": None,
        "in_bundle": None,
        "block": None,
        "tx": None,
        "cohort_size": None,
        "cohort_limit": None,
        "cohort_fresh_pct": None,
        "cohort_bundled_pct": None,
    }

    # ── Preferred: the own 250-wallet first-buyers engine (earliest buyer of
    #    the whole indexed cohort — deeper than the bundle's launch window).
    fbd = r.first_buyers if isinstance(r.first_buyers, dict) else None
    if fbd:
        agg = fbd.get("aggregates")
        if isinstance(agg, dict):
            section["cohort_size"] = agg.get("unique_buyers")
            section["cohort_fresh_pct"] = agg.get("fresh_wallet_pct")
            section["cohort_bundled_pct"] = agg.get("bundled_pct")
        section["cohort_limit"] = fbd.get("limit")
        buyers = fbd.get("buyers")
        if isinstance(buyers, list) and buyers:
            first = buyers[0]
            if isinstance(first, dict):
                section["address"] = first.get("wallet") or first.get("buyer") or first.get("address")
                section["tx"] = first.get("first_buy_ts")
                if isinstance(first.get("bundled"), bool):
                    section["in_bundle"] = first["bundled"]

    bundle = (
        bundle if isinstance(bundle, dict) else (r.bundle_analysis if isinstance(r.bundle_analysis, dict) else None)
    )
    if bundle:
        launch_buyers = bundle.get("launch_buyers")
        if isinstance(launch_buyers, list) and launch_buyers:
            first = launch_buyers[0]
            if isinstance(first, dict):
                section["address"] = first.get("address", first.get("buyer"))
                section["block"] = first.get("block_number", first.get("block"))
                section["tx"] = first.get("tx_hash")
            elif isinstance(first, str):
                section["address"] = first

        # Deployer-funded / first-time buyers in the launch window.
        deployer_funded = bundle.get("deployer_funded") or []
        if isinstance(deployer_funded, list):
            for w in deployer_funded:
                addr = w.get("address") if isinstance(w, dict) else w
                if isinstance(addr, str) and section.get("address") and addr.lower() == str(section["address"]).lower():
                    section["is_deployer"] = False
                    break

        if isinstance(bundle.get("cluster_blocks"), list) and bundle["cluster_blocks"]:
            section["in_bundle"] = section.get("block") is not None or bool(bundle.get("is_bundled"))
        if section.get("in_bundle") is None:
            # Keep None when the bundle engine did not run (unknown), not False.
            section["in_bundle"] = bundle.get("is_bundled")

    if section.get("address") and deployer and str(section["address"]).lower() == deployer.lower():
        section["is_deployer"] = True
    return section


def _extract_bundle_section(r: ScanResult, bundle: dict[str, Any] | None) -> dict[str, Any]:
    """Bundle/sniper launch section from own_bundle_detect output."""
    bundle = (
        bundle if isinstance(bundle, dict) else (r.bundle_analysis if isinstance(r.bundle_analysis, dict) else None)
    )
    if not bundle or bundle.get("result") != "scanned":
        return {"is_bundled": None, "confidence": None, "source": "unavailable"}
    sniper_count = 0
    cluster_blocks = bundle.get("cluster_blocks") or []
    if isinstance(cluster_blocks, list):
        for b in cluster_blocks:
            if isinstance(b, dict):
                sniper_count += _i(b.get("buyer_count"))
    return {
        "is_bundled": bool(bundle.get("is_bundled")) if bundle.get("is_bundled") is not None else None,
        "confidence": _f(bundle.get("confidence")),
        "launch_cluster_score": _f(bundle.get("launch_cluster_score")),
        "first_time_buyer_pct": _f(bundle.get("first_time_buyer_pct")),
        "deployer_funded_pct": _f(bundle.get("deployer_funded_pct")),
        "common_funder_score": _f(bundle.get("common_funder_score")),
        "similar_amount_score": _f(bundle.get("similar_amount_score")),
        "cluster_blocks": cluster_blocks[:10],
        "sniper_first_buyers": sniper_count,
        "launch_buyers": bundle.get("launch_buyers"),
        "source": "own_bundle_detect",
    }


def _extract_honeypot_section(r: ScanResult) -> dict[str, Any]:
    """Honeypot + mint/freeze/ownership vectors from security + SENTINEL."""
    sec = _sec(r)
    hp = _sentinel_module(r, "honeypot_detection")
    ca = _sentinel_module(r, "contract_authority")

    is_honeypot = bool(sec.get("is_honeypot"))
    if not is_honeypot:
        is_honeypot = bool(hp.get("is_honeypot"))

    buy_tax = _f(sec.get("buy_tax_pct", sec.get("buy_tax")))
    sell_tax = _f(sec.get("sell_tax_pct", sec.get("sell_tax")))
    if buy_tax is None:
        buy_tax = _f(hp.get("buy_tax"))
    if sell_tax is None:
        sell_tax = _f(hp.get("sell_tax"))

    can_sell = hp.get("can_sell")
    if can_sell is None:
        can_sell = False if is_honeypot else None

    mintable = sec.get("is_mintable")
    if mintable is None:
        mintable = ca.get("can_mint", ca.get("mint_authority_active"))
    freezable = sec.get("is_freezable")
    if freezable is None:
        freezable = ca.get("can_freeze", ca.get("freeze_authority_active"))

    ownership_renounced = sec.get("ownership_renounced", sec.get("is_owner_renounced"))
    if ownership_renounced is None:
        ownership_renounced = ca.get("is_ownership_renounced")

    return {
        "is_honeypot": is_honeypot
        if is_honeypot
        else (None if hp.get("is_honeypot") is None and sec.get("is_honeypot") is None else False),
        "can_sell": can_sell,
        "buy_tax": buy_tax,
        "sell_tax": sell_tax,
        "transfer_tax": _f(hp.get("transfer_tax")),
        "mintable": mintable,
        "freezable": freezable,
        "ownership_renounced": ownership_renounced,
        "warnings": hp.get("warnings") or [],
        "confidence": _i(hp.get("confidence")),
        "sources": [s for s in ("security", "sentinel_honeypot", "sentinel_contract_authority") if s],
    }


def _extract_liquidity_section(r: ScanResult) -> dict[str, Any]:
    """Liquidity lock + amount from unified liquidity / DataBus / own engine."""
    liq = r.liquidity if isinstance(r.liquidity, dict) else {}
    locked_pct = _f(liq.get("locked_pct"))
    return {
        "locked_pct": locked_pct,
        "is_locked": liq.get("is_locked")
        if liq.get("is_locked") is not None
        else (locked_pct is not None and locked_pct >= 50),
        "is_burned": liq.get("is_burned"),
        "lock_contract": liq.get("lock_contract", liq.get("lock_contract_name")),
        "liquidity_usd": _f(r.liquidity_usd) if r.liquidity_usd else _f(liq.get("liquidity_usd")),
        "source": liq.get("source", "unknown"),
    }
