"""Structured RiskReport — deployer / holders / first-buyer / top-25 / fresh /
CEX-funded / bundle / cluster / honeypot / liquidity, per-factor explanation +
composite score + high-level rug/scam determination.

The unified scanner produces a rich ``ScanResult``: every phase (core security,
holder data, clever-wallet funding analysis, bundle detection, SENTINEL
deep-forensic modules) leaves its payload on the result. This module turns all
of that into ONE structured, human-readable risk report:

  * ``RiskFactor``  — one explainable factor (key, label, 0-100 risk score,
                      status ok|risk|critical|unknown, why-it's-risky text).
  * ``ClusterInfo`` — the beyond-market CLUSTER payload (funder tree,
                      shared-deployer network, cross-token cluster graph,
                      cross-token funder graph, temporal lockstep, sniper/bot
                      patterns, MEV bundles, wash-trading clusters).
  * ``RiskReport``  — the full report: deployer, holders, first buyer,
                      top-25 holders, fresh %, CEX-funded %, bundle, cluster,
                      honeypot, liquidity + ``factors`` (~25) + composite
                      score + determination (scam/rug/risky/ok) + deeper
                      analysis + data-quality metadata.
  * ``build_risk_report`` — pure aggregation from a ``ScanResult`` (plus the
                      optional injectable engine payloads). Never raises;
                      every unavailable factor is marked ``unknown``.
  * ``format_risk_report`` — the formatter: structured text output with
                      per-factor explanations and (on ``detailed=True``) the
                      expanded deeper-level analysis.
  * ``run_risk_report_phase`` — async scanner-phase wiring: resolves the
                      deployer, runs the deployer fingerprint / holder-cluster
                      / wallet-cluster engines (best-effort) and attaches
                      ``ScanResult.risk_report``.

Design rules
------------
- Pure builder: ``build_risk_report`` is synchronous and only reads already
  fetched data, so it is trivially testable with a synthetic ``ScanResult``
  and never touches the network / ClickHouse.
- Fail-open: every field extraction is defensive — a missing dict key leaves
  the factor at ``unknown`` instead of raising.
- The composite score is a weighted average over KNOWN factors only; the
  data-quality block reports how many factors were known so callers can
  discount a score built from thin data.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.domains.scanner.engines._logging import get_logger

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)

__all__ = [
    "ClusterInfo",
    "RiskFactor",
    "RiskReport",
    "build_risk_report",
    "format_risk_report",
    "run_risk_report_phase",
]

# ── Thresholds (risk bands shared by every factor) ────────────────────────
_CRITICAL_MIN = 70.0  # factor score >= 70 → CRITICAL
_RISK_MIN = 25.0  # factor score >= 25 → RISK (else OK)

# ── Determination bands (composite risk score) ────────────────────────────
_SCAM_MIN = 85.0  # composite >= 85 → scam (or confirmed honeypot)
_RUG_MIN = 70.0  # composite >= 70 → rug (or rugged-dev + bundle)
_RISKY_MIN = 45.0  # composite >= 45 → risky
_MIN_KNOWN_FOR_DETERMINATION = 6  # need >= 6 known factors to declare a verdict


# ═══════════════════════════════════════════════════════════════════════════
# DATA MODEL
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class RiskFactor:
    """One explainable risk factor.

    ``risk_score`` is 0-100 (higher = riskier); ``None`` means the data was
    unavailable and the factor is reported as ``unknown`` (never crashes the
    scan). ``explanation`` states WHY the current value is (or is not) risky.
    """

    key: str
    label: str
    risk_score: float | None = None
    status: str = "unknown"  # "ok" | "risk" | "critical" | "unknown"
    explanation: str = ""


@dataclass
class ClusterInfo:
    """Beyond-market wallet-cluster detection payload."""

    funder_tree: list[dict[str, Any]] = field(default_factory=list)
    shared_deployer_network: list[dict[str, Any]] = field(default_factory=list)
    cross_token_clusters: list[dict[str, Any]] = field(default_factory=list)
    # Deepened structures (risk_cluster legs 4-8).
    cross_token_funders: list[dict[str, Any]] = field(default_factory=list)
    temporal_clusters: list[dict[str, Any]] = field(default_factory=list)
    sniper_patterns: list[dict[str, Any]] = field(default_factory=list)
    mev_bundles: list[dict[str, Any]] = field(default_factory=list)
    wash_clusters: dict[str, Any] = field(default_factory=dict)
    cluster_risk_score: int | None = None  # 0-100, None = unavailable
    cluster_risk_label: str = "unknown"  # LOW/MEDIUM/HIGH/CRITICAL/unknown
    description: str = ""


@dataclass
class RiskReport:
    """The complete structured scanner output for one token."""

    token_address: str
    chain: str
    scanned_at: str = ""
    tier: str = ""

    # ── Sections ──
    deployer: dict[str, Any] = field(default_factory=dict)
    holders: dict[str, Any] = field(default_factory=dict)
    first_buyer: dict[str, Any] = field(default_factory=dict)
    top_25_holders: list[dict[str, Any]] = field(default_factory=list)
    fresh_wallet_pct: float | None = None
    cex_funded_wallet_pct: float | None = None
    bundle: dict[str, Any] = field(default_factory=dict)
    cluster: ClusterInfo = field(default_factory=ClusterInfo)
    honeypot: dict[str, Any] = field(default_factory=dict)
    liquidity: dict[str, Any] = field(default_factory=dict)

    # ── Score + verdict ──
    factors: list[RiskFactor] = field(default_factory=list)
    composite_score: int | None = None  # 0-100 risk, None when nothing known
    determination: str = "unknown"  # "scam" | "rug" | "risky" | "ok" | "unknown"
    summary: str = ""

    # ── Deeper-level expanded analysis (per-factor natural-language) ──
    deeper_analysis: dict[str, Any] = field(default_factory=dict)
    ai_assisted: bool = False  # set True when an AI pass is applied downstream

    # ── Data quality ──
    data_quality: dict[str, Any] = field(default_factory=dict)
    sources_used: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict (used for persistence / API output)."""
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "scanned_at": self.scanned_at,
            "tier": self.tier,
            "deployer": self.deployer,
            "holders": self.holders,
            "first_buyer": self.first_buyer,
            "top_25_holders": self.top_25_holders,
            "fresh_wallet_pct": self.fresh_wallet_pct,
            "cex_funded_wallet_pct": self.cex_funded_wallet_pct,
            "bundle": self.bundle,
            "cluster": {
                "funder_tree": self.cluster.funder_tree,
                "shared_deployer_network": self.cluster.shared_deployer_network,
                "cross_token_clusters": self.cluster.cross_token_clusters,
                "cross_token_funders": self.cluster.cross_token_funders,
                "temporal_clusters": self.cluster.temporal_clusters,
                "sniper_patterns": self.cluster.sniper_patterns,
                "mev_bundles": self.cluster.mev_bundles,
                "wash_trading": self.cluster.wash_clusters,
                "cluster_risk_score": self.cluster.cluster_risk_score,
                "cluster_risk_label": self.cluster.cluster_risk_label,
                "description": self.cluster.description,
            },
            "honeypot": self.honeypot,
            "liquidity": self.liquidity,
            "factors": [
                {
                    "key": f.key,
                    "label": f.label,
                    "risk_score": f.risk_score,
                    "status": f.status,
                    "explanation": f.explanation,
                }
                for f in self.factors
            ],
            "composite_score": self.composite_score,
            "determination": self.determination,
            "summary": self.summary,
            "deeper_analysis": self.deeper_analysis,
            "ai_assisted": self.ai_assisted,
            "data_quality": self.data_quality,
            "sources_used": self.sources_used,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskReport:
        """Rehydrate a ``RiskReport`` from ``to_dict()`` output (pure round-trip).

        Consumers that receive the serialized report — API responses, the
        Redis result cache, the Telegram bot formatter — can rebuild the typed
        dataclass without re-running the scanner. Defensive: missing keys fall
        back to the dataclass defaults, so a truncated payload never raises.
        """
        cluster_raw = data.get("cluster")
        cluster_src: dict[str, Any] = cluster_raw if isinstance(cluster_raw, dict) else {}
        cluster = ClusterInfo(
            funder_tree=list(cluster_src.get("funder_tree") or []),
            shared_deployer_network=list(cluster_src.get("shared_deployer_network") or []),
            cross_token_clusters=list(cluster_src.get("cross_token_clusters") or []),
            cross_token_funders=list(cluster_src.get("cross_token_funders") or []),
            temporal_clusters=list(cluster_src.get("temporal_clusters") or []),
            sniper_patterns=list(cluster_src.get("sniper_patterns") or []),
            mev_bundles=list(cluster_src.get("mev_bundles") or []),
            wash_clusters=dict(cluster_src.get("wash_trading") or {}),
            cluster_risk_score=cluster_src.get("cluster_risk_score"),
            cluster_risk_label=str(cluster_src.get("cluster_risk_label") or "unknown"),
            description=str(cluster_src.get("description") or ""),
        )
        factors = [
            RiskFactor(
                key=str(f.get("key") or "unknown"),
                label=str(f.get("label") or f.get("key") or "unknown"),
                risk_score=f.get("risk_score"),
                status=str(f.get("status") or "unknown"),
                explanation=str(f.get("explanation") or ""),
            )
            for f in data.get("factors") or []
            if isinstance(f, dict)
        ]
        return cls(
            token_address=str(data.get("token_address") or ""),
            chain=str(data.get("chain") or ""),
            scanned_at=str(data.get("scanned_at") or ""),
            tier=str(data.get("tier") or ""),
            deployer=dict(data.get("deployer") or {}),
            holders=dict(data.get("holders") or {}),
            first_buyer=dict(data.get("first_buyer") or {}),
            top_25_holders=list(data.get("top_25_holders") or []),
            fresh_wallet_pct=data.get("fresh_wallet_pct"),
            cex_funded_wallet_pct=data.get("cex_funded_wallet_pct"),
            bundle=dict(data.get("bundle") or {}),
            cluster=cluster,
            honeypot=dict(data.get("honeypot") or {}),
            liquidity=dict(data.get("liquidity") or {}),
            factors=factors,
            composite_score=data.get("composite_score"),
            determination=str(data.get("determination") or "unknown"),
            summary=str(data.get("summary") or ""),
            deeper_analysis=dict(data.get("deeper_analysis") or {}),
            ai_assisted=bool(data.get("ai_assisted")),
            data_quality=dict(data.get("data_quality") or {}),
            sources_used=list(data.get("sources_used") or []),
        )


# ═══════════════════════════════════════════════════════════════════════════
# FACTOR WEIGHTS (composite = weighted avg over known factors)
# ═══════════════════════════════════════════════════════════════════════════

# ~25 factors, one weight per factor. Weights are relative importance within
# the composite (higher = counts more). The decisive factors (honeypot, mint,
# bundle) are weighted most heavily.
FACTOR_WEIGHTS: dict[str, float] = {
    # Deployer (4)
    "deployer_repeat": 2.0,
    "deployer_rugged_before": 3.0,
    "deployer_new_wallet": 1.5,
    "deployer_funding": 1.5,
    # Holders (5)
    "holder_concentration": 2.5,
    "dev_team_share": 2.5,
    "top_holder_whale": 1.5,
    "fresh_wallet_pct": 2.0,
    "cex_funded_wallet_pct": 1.0,
    # Launch (4)
    "first_buyer_deployer": 1.5,
    "first_buyer_bundle": 2.0,
    "bundle_launch": 3.0,
    "sniper_first_buyers": 1.5,
    # Cluster (8)
    "funder_cluster": 2.5,
    "cross_token_cluster": 2.0,
    "shared_deployer_network": 2.5,
    "cross_token_funder": 2.0,
    "temporal_cluster": 2.0,
    "sniper_bot_pattern": 2.0,
    "mev_bundle": 2.5,
    "wash_trading_cluster": 2.5,
    # Honeypot / contract (4)
    "honeypot": 5.0,
    "mint_authority": 3.0,
    "freeze_authority": 2.0,
    "ownership_renounced": 2.0,
    # Liquidity / tax / market (5)
    "lp_locked": 3.0,
    "lp_liquidity_amount": 1.5,
    "buy_sell_tax": 1.5,
    "volume_authenticity": 1.5,
    "holder_overlap_scam": 2.0,
}

FACTOR_LABELS: dict[str, str] = {
    "deployer_repeat": "Repeat deployer",
    "deployer_rugged_before": "Deployer previously rugged",
    "deployer_new_wallet": "Fresh deployer wallet",
    "deployer_funding": "Deployer funding source",
    "holder_concentration": "Holder concentration (top-10)",
    "dev_team_share": "Dev/team wallet share",
    "top_holder_whale": "Whale top holder",
    "fresh_wallet_pct": "Fresh wallet share",
    "cex_funded_wallet_pct": "CEX-funded wallet share",
    "first_buyer_deployer": "First buyer is deployer",
    "first_buyer_bundle": "First buyer in bundle",
    "bundle_launch": "Bundled launch",
    "sniper_first_buyers": "Sniper first-buyers",
    "funder_cluster": "Shared-funder cluster",
    "cross_token_cluster": "Cross-token wallet cluster",
    "shared_deployer_network": "Shared-deployer network",
    "cross_token_funder": "Cross-token funder graph",
    "temporal_cluster": "Lockstep entry (temporal) cluster",
    "sniper_bot_pattern": "Sniper/bot launch pattern",
    "mev_bundle": "MEV bundle buys",
    "wash_trading_cluster": "Wash-trading cluster",
    "honeypot": "Honeypot (cannot sell)",
    "mint_authority": "Mint authority active",
    "freeze_authority": "Freeze authority active",
    "ownership_renounced": "Ownership not renounced",
    "lp_locked": "Liquidity locked",
    "lp_liquidity_amount": "Liquidity amount",
    "buy_sell_tax": "Buy/sell tax",
    "volume_authenticity": "Volume authenticity",
    "holder_overlap_scam": "Holder overlap with known scams",
}


# ═══════════════════════════════════════════════════════════════════════════
# FACTOR CONSTRUCTION
# ═══════════════════════════════════════════════════════════════════════════


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


# ═══════════════════════════════════════════════════════════════════════════
# THE ~25 FACTORS
# ═══════════════════════════════════════════════════════════════════════════


def _build_factors(
    r: ScanResult,
    deployer_section: dict[str, Any],
    holders_section: dict[str, Any],
    first_buyer: dict[str, Any],
    bundle: dict[str, Any],
    cluster_info: ClusterInfo,
    honeypot: dict[str, Any],
    liquidity: dict[str, Any],
    fresh_pct: float | None,
    cex_pct: float | None,
) -> list[RiskFactor]:
    """Compute the ~25 factors with per-factor WHY-it's-risky explanations."""
    factors: list[RiskFactor] = []
    add = factors.append

    # ── Deployer (4) ──
    repeat = deployer_section.get("repeat_deployer")
    total = _i(deployer_section.get("total_deployments"))
    if repeat is None:
        add(_factor("deployer_repeat", None, "No deployer history data available."))
    else:
        score = 45.0 if repeat else 5.0
        add(
            _factor(
                "deployer_repeat",
                score,
                f"Deployer has launched {total} token(s); repeat deployers can recycle wallets across launches "
                + ("(risky — same wallet, new token)." if repeat else "but shows no repeat-launch pattern."),
            )
        )

    rugged = deployer_section.get("previously_rugged")
    bucket = deployer_section.get("risk_bucket")
    if rugged is None:
        add(_factor("deployer_rugged_before", None, "No prior-deployment outcome data available."))
    else:
        add(
            _factor(
                "deployer_rugged_before",
                80.0 if rugged else 10.0,
                f"Deployer fingerprint bucket '{bucket}': prior launches that died within 7 days "
                + ("indicate a serial-rug pattern." if rugged else "survived past the 7-day window."),
            )
        )

    first_seen = deployer_section.get("first_seen")
    if first_seen:
        add(
            _factor(
                "deployer_new_wallet",
                40.0,
                f"Deployer first seen {first_seen} — recent first activity on a fresh wallet is a farm signal.",
            )
        )
    else:
        add(_factor("deployer_new_wallet", None, "Deployer wallet age unknown."))

    funder = deployer_section.get("funder") or {}
    if funder.get("address"):
        is_cex = bool(funder.get("is_cex"))
        is_fresh = bool(funder.get("is_fresh"))
        score = 10.0 if is_cex else (60.0 if is_fresh else 30.0)
        add(
            _factor(
                "deployer_funding",
                score,
                f"Deployer funded by {'CEX ' + str(funder.get('cex_label')) if is_cex else 'a non-exchange wallet'} "
                + ("(fresh funder with <10 txs)." if is_fresh and not is_cex else "."),
            )
        )
    else:
        add(_factor("deployer_funding", None, "Deployer funding source unknown."))

    # ── Holders (5) ──
    top10 = _f(holders_section.get("top_10_pct"))
    if top10 is None:
        add(_factor("holder_concentration", None, "Top-10 holder share unavailable."))
    else:
        score = 0.0 if top10 < 30 else (35.0 if top10 < 50 else (65.0 if top10 < 80 else 90.0))
        add(
            _factor(
                "holder_concentration",
                score,
                f"Top-10 holders control {top10:.1f}% of tracked supply — "
                + ("distributed." if score == 0 else "a rug/exit-liquidity risk if they coordinate."),
            )
        )

    dev_team = _f(holders_section.get("dev_team_share"))
    if dev_team is None:
        add(_factor("dev_team_share", None, "Dev/team wallet share unavailable."))
    else:
        add(
            _factor(
                "dev_team_share",
                0.0 if dev_team < 10 else (50.0 if dev_team < 25 else 85.0),
                f"Deployer + team wallets hold {dev_team:.1f}% — "
                + (
                    "large insider share enables coordinated dumps." if dev_team >= 25 else "moderate insider presence."
                ),
            )
        )

    top25 = _extract_top25_pcts(r, holders_section)
    whale = top25[0] if top25 else None
    if whale is None:
        add(_factor("top_holder_whale", None, "Top holder share unavailable."))
    else:
        add(
            _factor(
                "top_holder_whale",
                0.0 if whale < 15 else (55.0 if whale < 30 else 85.0),
                f"Largest holder controls {whale:.1f}% — "
                + ("normal." if whale < 15 else "a single wallet selling is a price-crash risk."),
            )
        )

    fresh_pct = fresh_pct
    if fresh_pct is None:
        add(_factor("fresh_wallet_pct", None, "Fresh-wallet % unavailable."))
    else:
        score = 0.0 if fresh_pct < 20 else (40.0 if fresh_pct < 40 else 75.0)
        add(
            _factor(
                "fresh_wallet_pct",
                score,
                f"{fresh_pct:.1f}% of analyzed holders are fresh wallets (<30 days) — "
                + ("a bot-farm / airdrop-farming concentration signal." if score >= 40 else "within normal bounds."),
            )
        )

    cex_pct = cex_pct
    if cex_pct is None:
        cex_pct = _f((r.wallet_metrics or {}).get("cex_funded_pct"))
    if cex_pct is None:
        cex_pct = _f((r.funding_analysis or {}).get("exchange_funded_pct"))
    if cex_pct is None:
        add(_factor("cex_funded_wallet_pct", None, "CEX-funded wallet % unavailable."))
    else:
        # High CEX funding reads as institutional backing (positive); very low
        # with high fresh % is the farm pattern.
        score = 15.0 if cex_pct >= 40 else (40.0 if cex_pct < 10 else 20.0)
        add(
            _factor(
                "cex_funded_wallet_pct",
                score,
                f"{cex_pct:.1f}% of holders were funded by CEX wallets — "
                + (
                    "institutional backing." if score <= 15 else "low exchange backing leaves the holder base unvetted."
                ),
            )
        )

    # ── Launch (4) ──
    fb_addr = first_buyer.get("address")
    if not fb_addr:
        add(_factor("first_buyer_deployer", None, "First-buyer data unavailable."))
    else:
        is_deployer = bool(first_buyer.get("is_deployer"))
        add(
            _factor(
                "first_buyer_deployer",
                70.0 if is_deployer else 5.0,
                f"First buyer is {'the deployer' if is_deployer else 'not the deployer'} ({str(fb_addr)[:10]}…).",
            )
        )

    in_bundle = first_buyer.get("in_bundle")
    if in_bundle is None:
        add(_factor("first_buyer_bundle", None, "First-buyer bundle membership unknown."))
    else:
        add(
            _factor(
                "first_buyer_bundle",
                70.0 if in_bundle else 5.0,
                "First buyer entered in the same launch cluster as other wallets — "
                + ("classic bundle-buy opening." if in_bundle else "bought independently."),
            )
        )

    is_bundled = bundle.get("is_bundled")
    if is_bundled is None:
        add(_factor("bundle_launch", None, "Bundle-launch data unavailable."))
    else:
        launch_score = _f(bundle.get("launch_cluster_score")) or 0.0
        score = 80.0 if is_bundled else (45.0 if launch_score >= 50 else 5.0)
        if is_bundled:
            explanation = (
                "Launch bundle DETECTED (coordinated buys at deployment) — "
                "a top rug vector where the same group controls the opening supply."
            )
        else:
            explanation = f"Launch bundle not detected (launch cluster score {launch_score:.0f}/100)."
        add(_factor("bundle_launch", score, explanation))

    snipers = _i(bundle.get("sniper_first_buyers"))
    if bundle.get("sniper_first_buyers") is None:
        add(_factor("sniper_first_buyers", None, "Sniper first-buyer data unavailable."))
    else:
        score = 0.0 if snipers == 0 else (40.0 if snipers < 5 else 70.0)
        add(
            _factor(
                "sniper_first_buyers",
                score,
                f"{snipers} sniper first-buyers in launch clusters — "
                + (
                    "dev-controlled or MEV bots opening positions." if score >= 40 else "no meaningful sniper presence."
                ),
            )
        )

    # ── Cluster (3) ──
    cluster_score = cluster_info.cluster_risk_score
    if cluster_score is None:
        add(_factor("funder_cluster", None, "Cluster analysis unavailable."))
    else:
        roots = [c for c in cluster_info.funder_tree if c.get("funder_is_deployer")]
        add(
            _factor(
                "funder_cluster",
                min(90.0, 35.0 * len(roots) + 20.0 * max(len(cluster_info.funder_tree) - len(roots), 0)),
                f"{len(cluster_info.funder_tree)} shared-funder root(s) "
                + (
                    f"({len(roots)} deployer-funded) — a coordinated wallet group."
                    if cluster_info.funder_tree
                    else "— no shared funders."
                ),
            )
        )

    if cluster_score is None:
        add(_factor("cross_token_cluster", None, "Cross-token cluster data unavailable."))
    else:
        n = len(cluster_info.cross_token_clusters)
        add(
            _factor(
                "cross_token_cluster",
                min(75.0, 15.0 * n),
                f"{n} other launch(es) share >=2 of this token's holders — "
                + ("wallets move between launches together (sybil ring)." if n else "no cross-token co-appearance."),
            )
        )

    if cluster_score is None:
        add(_factor("shared_deployer_network", None, "Shared-deployer network data unavailable."))
    else:
        n = len(cluster_info.shared_deployer_network)
        add(
            _factor(
                "shared_deployer_network",
                min(90.0, 25.0 * n),
                f"{n} repeat-buyer ring(s) across launches by the same deployer — "
                + ("same wallets, new tokens = wash-deploy pattern." if n else "no shared-deployer ring detected."),
            )
        )

    # ── Deepened cluster factors (cross-token funder graph, lockstep, bot/MEV/wash) ──
    if cluster_score is None:
        add(_factor("cross_token_funder", None, "Cross-token funder graph data unavailable."))
    else:
        n = len(cluster_info.cross_token_funders)
        add(
            _factor(
                "cross_token_funder",
                min(80.0, 20.0 * n),
                f"{n} funder(s) seeded wallets across >=2 other launches — "
                + ("a capital source behind a coordinated launch network." if n else "no cross-token funder graph."),
            )
        )

    if cluster_score is None:
        add(_factor("temporal_cluster", None, "Temporal (lockstep) cluster data unavailable."))
    else:
        n = len(cluster_info.temporal_clusters)
        max_members = max((int(c.get("members") or 0) for c in cluster_info.temporal_clusters), default=0)
        add(
            _factor(
                "temporal_cluster",
                min(80.0, 15.0 * n + 8.0 * max_members),
                f"{n} lockstep group(s) (largest {max_members} wallets) entered in the same launch blocks — "
                + ("wallets acting in lockstep." if n else "no lockstep entry groups."),
            )
        )

    if cluster_score is None:
        add(_factor("sniper_bot_pattern", None, "Sniper/bot pattern data unavailable."))
    else:
        patterns = list(cluster_info.sniper_patterns)
        n = len(patterns)
        bot_score = 0.0
        for p in patterns:
            ptype = p.get("pattern")
            if ptype == "sniper_wave":
                bot_score += 25.0
            elif ptype == "same_block_snipe":
                bot_score += 20.0
            elif ptype in ("uniform_bot_amounts", "fresh_wallet_bots"):
                bot_score += 15.0
            elif ptype == "tip_cluster":
                bot_score += 10.0
            else:
                bot_score += 10.0
        bot_score = min(90.0, bot_score)
        names = ", ".join(str(p.get("pattern")) for p in patterns[:5])
        add(
            _factor(
                "sniper_bot_pattern",
                bot_score,
                f"{n} sniper/bot signature(s) at launch ({names or 'none'}) — "
                + ("bot-driven opening distribution." if bot_score >= 40 else "no strong bot-launch signature."),
            )
        )

    if cluster_score is None:
        add(_factor("mev_bundle", None, "MEV-bundle data unavailable."))
    else:
        n = len(cluster_info.mev_bundles)
        add(
            _factor(
                "mev_bundle",
                min(85.0, 20.0 * n),
                f"{n} MEV-bundle pattern(s) (atomic multi-wallet buys / buy-then-distribute) — "
                + ("bundled transactions simulate organic interest." if n else "no MEV-bundle signature."),
            )
        )

    if cluster_score is None:
        add(_factor("wash_trading_cluster", None, "Wash-trading data unavailable."))
    else:
        wash = cluster_info.wash_clusters if isinstance(cluster_info.wash_clusters, dict) else {}
        cycles = wash.get("cycles")
        n_cycles = len(cycles) if isinstance(cycles, list) else 0
        ratio = _f(wash.get("cycle_volume_ratio"))
        wash_score_val = min(90.0, 12.0 * n_cycles + 60.0 * (ratio or 0.0))
        ratio_txt = f" covering {ratio * 100:.1f}% of transfer volume" if ratio else ""
        add(
            _factor(
                "wash_trading_cluster",
                wash_score_val,
                f"{n_cycles} wash-trade cycle(s){ratio_txt} — "
                + (
                    "circular trading inflates volume and fakes activity."
                    if wash_score_val >= 40
                    else "no meaningful circular trading."
                ),
            )
        )

    # ── Honeypot / contract (4) ──
    hp = honeypot.get("is_honeypot")
    if hp is None:
        add(_factor("honeypot", None, "Honeypot status unknown (no simulation data)."))
    else:
        add(
            _factor(
                "honeypot",
                95.0 if hp else 0.0,
                "Honeypot DETECTED — the token can be bought but not sold; "
                + "holders cannot exit, a definitive scam vector."
                if hp
                else "Sell-side simulation passes — not a honeypot.",
            )
        )

    mintable = honeypot.get("mintable")
    if mintable is None:
        add(_factor("mint_authority", None, "Mint authority unknown."))
    else:
        add(
            _factor(
                "mint_authority",
                85.0 if mintable else 5.0,
                "Mint authority is ACTIVE — the deployer can mint unlimited supply and dump on holders."
                if mintable
                else "Mint authority renounced — supply cannot be inflated.",
            )
        )

    freezable = honeypot.get("freezable")
    if freezable is None:
        add(_factor("freeze_authority", None, "Freeze authority unknown."))
    else:
        add(
            _factor(
                "freeze_authority",
                70.0 if freezable else 5.0,
                "Freeze authority is ACTIVE — holders can be frozen out of their positions."
                if freezable
                else "Freeze authority renounced.",
            )
        )

    renounced = honeypot.get("ownership_renounced")
    if renounced is None:
        add(_factor("ownership_renounced", None, "Ownership renunciation unknown."))
    else:
        add(
            _factor(
                "ownership_renounced",
                5.0 if renounced else 50.0,
                "Ownership renounced — contract owner cannot change rules."
                if renounced
                else "Ownership NOT renounced — the owner retains control (tax changes, blacklist, upgrade).",
            )
        )

    # ── Liquidity / tax / market (5) ──
    locked = liquidity.get("locked_pct")
    if locked is None:
        add(_factor("lp_locked", None, "LP lock status unavailable."))
    else:
        add(
            _factor(
                "lp_locked",
                0.0 if locked >= 50 else (55.0 if locked >= 20 else 85.0),
                f"{locked:.1f}% of LP is locked/burned — "
                + (
                    "liquidity cannot be pulled."
                    if locked >= 50
                    else "unlocked LP is a rug vector (dev can remove it)."
                ),
            )
        )

    liq_usd = liquidity.get("liquidity_usd")
    if liq_usd is None:
        add(_factor("lp_liquidity_amount", None, "Liquidity amount unavailable."))
    else:
        add(
            _factor(
                "lp_liquidity_amount",
                0.0 if liq_usd >= 100_000 else (45.0 if liq_usd >= 20_000 else 75.0),
                f"Liquidity is ~${liq_usd:,.0f} — "
                + (
                    "deep enough to absorb sells."
                    if liq_usd >= 100_000
                    else "thin liquidity amplifies exit-sell price impact."
                ),
            )
        )

    buy_tax = honeypot.get("buy_tax")
    sell_tax = honeypot.get("sell_tax")
    if buy_tax is None and sell_tax is None:
        add(_factor("buy_sell_tax", None, "Tax data unavailable."))
    else:
        b = _f(buy_tax) or 0.0
        s = _f(sell_tax) or 0.0
        combined = b + s
        score = 0.0 if combined < 10 else (40.0 if combined < 25 else 80.0)
        add(
            _factor(
                "buy_sell_tax",
                score,
                f"Buy tax {b:.1f}% / sell tax {s:.1f}% — "
                + ("combined tax is a profit-trap for holders." if score >= 40 else "taxes within normal bounds."),
            )
        )

    va = r.volume_auth if isinstance(r.volume_auth, dict) else {}
    fake_pct = _f(va.get("fake_volume_pct"))
    if fake_pct is None:
        add(_factor("volume_authenticity", None, "Volume authenticity unavailable."))
    else:
        add(
            _factor(
                "volume_authenticity",
                0.0 if fake_pct < 10 else (50.0 if fake_pct < 50 else 85.0),
                f"{fake_pct:.1f}% of volume is estimated fake — "
                + (
                    "washed volume creates a false liquidity/momentum picture."
                    if fake_pct >= 50
                    else "volume looks organic."
                ),
            )
        )

    overlap = r.holder_overlap if isinstance(r.holder_overlap, dict) else {}
    max_overlap = _f(overlap.get("max_overlap_pct"))
    worst_scam = bool(overlap.get("worst_token_is_scam"))
    if max_overlap is None:
        add(_factor("holder_overlap_scam", None, "Holder-overlap-vs-scams data unavailable."))
    else:
        score = 0.0 if max_overlap < 30 else (60.0 if not worst_scam else 85.0)
        add(
            _factor(
                "holder_overlap_scam",
                score,
                f"{max_overlap:.1f}% holder overlap with known scam tokens"
                + (" — the same wallets appear in confirmed scams." if worst_scam else " — elevated cross-holding."),
            )
        )

    return factors


def _extract_top25_pcts(r: ScanResult, holders_section: dict[str, Any]) -> list[float]:
    """Sorted top-holder percentages (descending) for whale/share math."""
    pcts: list[float] = []
    top_25 = holders_section.get("_top_25")
    if not isinstance(top_25, list):
        return pcts
    for h in top_25:
        if not isinstance(h, dict):
            continue
        p = h.get("share_pct")
        f = _f(p)
        if f is not None:
            pcts.append(f)
    return sorted(pcts, reverse=True)


# ═══════════════════════════════════════════════════════════════════════════
# COMPOSITE + DETERMINATION
# ═══════════════════════════════════════════════════════════════════════════


def _composite_and_determination(
    factors: list[RiskFactor],
    honeypot: dict[str, Any],
    deployer_section: dict[str, Any],
    bundle: dict[str, Any],
) -> tuple[int | None, str, str]:
    """Weighted-average composite over KNOWN factors + high-level verdict."""
    known = [f for f in factors if f.risk_score is not None]
    if not known:
        return None, "unknown", "Insufficient data to score this token."

    total_w = 0.0
    total = 0.0
    for f in known:
        if f.risk_score is None:
            continue
        w = FACTOR_WEIGHTS.get(f.key, 1.0)
        total += float(f.risk_score) * w
        total_w += w
    composite = int(round(total / total_w)) if total_w > 0 else 50

    determination: str
    if bool(honeypot.get("is_honeypot")) or composite >= _SCAM_MIN:
        determination = "scam"
    elif composite >= _RUG_MIN or (
        len(known) >= _MIN_KNOWN_FOR_DETERMINATION
        and bool(deployer_section.get("previously_rugged"))
        and bool(bundle.get("is_bundled"))
    ):
        determination = "rug"
    elif composite >= _RISKY_MIN:
        determination = "risky"
    elif composite < _RISKY_MIN:
        determination = "ok"
    else:
        determination = "unknown"

    summary = _summary_text(determination, composite, len(known), len(factors))
    return composite, determination, summary


def _summary_text(determination: str, composite: int | None, known: int, total: int) -> str:
    """Human-readable one-line summary of the verdict."""
    base = f"Composite risk {composite}/100 from {known}/{total} known factors."
    if determination == "scam":
        return f"{base} DETERMINATION: SCAM — decisive scam vectors detected; do not trade."
    if determination == "rug":
        return f"{base} DETERMINATION: RUG — strong rug-pull signals; extreme caution."
    if determination == "risky":
        return f"{base} DETERMINATION: RISKY — elevated risk; only trade with full awareness."
    if determination == "ok":
        return f"{base} DETERMINATION: OK — no dominant scam/rug signal detected."
    return f"{base} Determination: UNKNOWN — insufficient data."


# ═══════════════════════════════════════════════════════════════════════════
# DEEPER ANALYSIS (expanded per-factor, deeper scans + AI assistance hook)
# ═══════════════════════════════════════════════════════════════════════════


def _build_deeper_analysis(
    report_sections: dict[str, Any],
    factors: list[RiskFactor],
) -> dict[str, Any]:
    """Expanded natural-language analysis grouped by section.

    This is the "deeper-level" pass: every section gets a narrative that
    explains the evidence, not just the verdict. Callers may post-process
    ``deeper_analysis`` with an AI model (``ai_assisted=True``) to reword /
    extend it — the structure is stable for downstream rendering.
    """
    deployer = report_sections["deployer"]
    holders = report_sections["holders"]
    first_buyer = report_sections["first_buyer"]
    bundle = report_sections["bundle"]
    honeypot = report_sections["honeypot"]
    liquidity = report_sections["liquidity"]

    factor_map = {f.key: f for f in factors}

    def _line(key: str) -> str:
        f = factor_map.get(key)
        if f is None:
            return ""
        return f"- {f.label}: {f.explanation}"

    deployer_text = (
        f"Deployer {str(deployer.get('address') or 'unknown')[:12]}… has "
        f"{deployer.get('total_deployments', '?')} deployment(s); repeat={deployer.get('repeat_deployer')}, "
        f"previously_rugged={deployer.get('previously_rugged')}."
    )
    holders_text = (
        f"{holders.get('total_holders', '?')} holders; top-10 share "
        f"{holders.get('top_10_pct', '?')}%; concentration {holders.get('concentration', 'unknown')}."
    )
    first_buyer_text = (
        f"First buyer {str(first_buyer.get('address') or 'unknown')[:12]}… — "
        f"deployer={first_buyer.get('is_deployer')}, in_bundle={first_buyer.get('in_bundle')}."
    )
    bundle_text = (
        f"Launch bundle={bundle.get('is_bundled')} (cluster score {bundle.get('launch_cluster_score') or 0}/100, "
        f"deployer-funded {bundle.get('deployer_funded_pct') or 0}%, first-time buyers {bundle.get('first_time_buyer_pct') or 0}%)."
    )
    honeypot_text = (
        f"Honeypot={honeypot.get('is_honeypot')}; buy tax {honeypot.get('buy_tax') or 0}%, sell tax "
        f"{honeypot.get('sell_tax') or 0}%; mintable={honeypot.get('mintable')}, "
        f"freezable={honeypot.get('freezable')}, ownership_renounced={honeypot.get('ownership_renounced')}."
    )
    liquidity_text = (
        f"LP locked {liquidity.get('locked_pct') or 0}% (locked={liquidity.get('is_locked')}); "
        f"liquidity ~${liquidity.get('liquidity_usd') or 0:,.0f}."
    )

    return {
        "deployer": {
            "summary": deployer_text,
            "factors": [
                ln
                for ln in [
                    _line("deployer_repeat"),
                    _line("deployer_rugged_before"),
                    _line("deployer_new_wallet"),
                    _line("deployer_funding"),
                ]
                if ln
            ],
        },
        "holders": {
            "summary": holders_text,
            "factors": [
                ln
                for ln in [
                    _line("holder_concentration"),
                    _line("dev_team_share"),
                    _line("top_holder_whale"),
                    _line("fresh_wallet_pct"),
                    _line("cex_funded_wallet_pct"),
                ]
                if ln
            ],
        },
        "first_buyer": {
            "summary": first_buyer_text,
            "factors": [ln for ln in [_line("first_buyer_deployer"), _line("first_buyer_bundle")] if ln],
        },
        "launch": {
            "summary": bundle_text,
            "factors": [ln for ln in [_line("bundle_launch"), _line("sniper_first_buyers")] if ln],
        },
        "cluster": {
            "summary": (
                "Cluster graph: funder tree + shared-deployer networks + cross-token "
                "clusters + cross-token funder graphs + temporal lockstep + "
                "sniper/bot patterns + MEV bundles + wash-trading clusters."
            ),
            "factors": [
                ln
                for ln in [
                    _line("funder_cluster"),
                    _line("shared_deployer_network"),
                    _line("cross_token_cluster"),
                    _line("cross_token_funder"),
                    _line("temporal_cluster"),
                    _line("sniper_bot_pattern"),
                    _line("mev_bundle"),
                    _line("wash_trading_cluster"),
                ]
                if ln
            ],
        },
        "honeypot_contract": {
            "summary": honeypot_text,
            "factors": [
                ln
                for ln in [
                    _line("honeypot"),
                    _line("mint_authority"),
                    _line("freeze_authority"),
                    _line("ownership_renounced"),
                ]
                if ln
            ],
        },
        "liquidity_market": {
            "summary": liquidity_text,
            "factors": [
                ln
                for ln in [
                    _line("lp_locked"),
                    _line("lp_liquidity_amount"),
                    _line("buy_sell_tax"),
                    _line("volume_authenticity"),
                    _line("holder_overlap_scam"),
                ]
                if ln
            ],
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC BUILDER
# ═══════════════════════════════════════════════════════════════════════════


def build_risk_report(
    r: ScanResult,
    *,
    deployer_fingerprint: dict[str, Any] | None = None,
    holder_clusters: dict[str, Any] | None = None,
    cluster_graph: dict[str, Any] | None = None,
) -> RiskReport:
    """Build a full ``RiskReport`` from a scan result (pure, never raises).

    ``deployer_fingerprint`` / ``holder_clusters`` / ``cluster_graph`` are the
    outputs of the own ClickHouse engines (``deployer_fingerprint``,
    ``holder_clusters``, ``risk_cluster``); pass ``None`` when unavailable and
    the affected factors degrade to ``unknown``.
    """
    # ── Sections ──
    deployer_section, resolved_deployer = _extract_deployer_section(r, deployer_fingerprint, holder_clusters)
    holders_section, top_25, cluster_deployer = _extract_holders_section(r, holder_clusters)
    if resolved_deployer is None:
        resolved_deployer = cluster_deployer
    deployer_section.setdefault("address", resolved_deployer)

    bundle = _extract_bundle_section(r, r.bundle_analysis if isinstance(r.bundle_analysis, dict) else None)
    first_buyer = _extract_first_buyer_section(r, resolved_deployer, bundle)
    honeypot = _extract_honeypot_section(r)
    liquidity = _extract_liquidity_section(r)

    # ── Fresh / CEX % (token-level) ──
    fresh_pct = _f((r.wallet_metrics or {}).get("fresh_wallet_pct"))
    if fresh_pct is None:
        fresh_pct = _f((r.funding_analysis or {}).get("fresh_wallet_pct"))
    if fresh_pct is None:
        fresh_pct = _f((r.wallet_freshness or {}).get("fresh_wallet_pct"))
    cex_pct = _f((r.wallet_metrics or {}).get("cex_funded_pct"))
    if cex_pct is None:
        cex_pct = _f((r.funding_analysis or {}).get("exchange_funded_pct"))

    # ── Cluster section ──
    cluster_info = ClusterInfo()
    if isinstance(cluster_graph, dict):
        cluster_info.funder_tree = list(cluster_graph.get("funder_tree") or [])
        cluster_info.shared_deployer_network = list(cluster_graph.get("shared_deployer_network") or [])
        cluster_info.cross_token_clusters = list(cluster_graph.get("cross_token_clusters") or [])
        cluster_info.cross_token_funders = list(cluster_graph.get("cross_token_funders") or [])
        cluster_info.temporal_clusters = list(cluster_graph.get("temporal_clusters") or [])
        cluster_info.sniper_patterns = list(cluster_graph.get("sniper_patterns") or [])
        cluster_info.mev_bundles = list(cluster_graph.get("mev_bundles") or [])
        wash = cluster_graph.get("wash_trading")
        if isinstance(wash, dict):
            cluster_info.wash_clusters = wash
        if cluster_graph.get("result") == "scanned" and cluster_graph.get("cluster_risk_score") is not None:
            cluster_info.cluster_risk_score = _i(cluster_graph.get("cluster_risk_score"))
            cluster_info.cluster_risk_label = str(cluster_graph.get("cluster_risk_label") or "unknown")
            cluster_info.description = str(cluster_graph.get("cluster_risk_description") or "")

    # ── Factors (~25) ──
    holders_section["_top_25"] = top_25
    try:
        factors = _build_factors(
            r,
            deployer_section,
            holders_section,
            first_buyer,
            bundle,
            cluster_info,
            honeypot,
            liquidity,
            fresh_pct,
            cex_pct,
        )
    except Exception as exc:  # defensive — a factor bug must never crash the report
        logger.debug("risk_report_factors_failed", error=str(exc)[:160])
        factors = [
            RiskFactor(
                key="report_error",
                label="Report assembly",
                risk_score=None,
                status="unknown",
                explanation="Factor assembly failed; report degraded.",
            )
        ]
    holders_section.pop("_top_25", None)

    composite, determination, summary = _composite_and_determination(factors, honeypot, deployer_section, bundle)

    known = [f for f in factors if f.risk_score is not None]
    unknown = [f for f in factors if f.risk_score is None]
    critical = [f for f in known if f.status == "critical"]
    risk = [f for f in known if f.status == "risk"]

    sources_used: list[str] = []
    if r.security:
        sources_used.append("security")
    if r.sentinel:
        sources_used.append("sentinel")
    if r.holders:
        sources_used.append("holders")
    if r.funding_analysis:
        sources_used.append("funding_analysis")
    if r.wallet_metrics:
        sources_used.append("wallet_metrics")
    if r.bundle_analysis:
        sources_used.append("bundle_analysis")
    if deployer_fingerprint:
        sources_used.append("deployer_fingerprint")
    if holder_clusters:
        sources_used.append("holder_clusters")
    if cluster_graph:
        sources_used.append("risk_cluster")

    data_quality = {
        "factors_total": len(factors),
        "factors_known": len(known),
        "factors_unknown": len(unknown),
        "critical_factors": len(critical),
        "risk_factors": len(risk),
        "ok_factors": len([f for f in known if f.status == "ok"]),
        "coverage_pct": round(len(known) / len(factors) * 100, 1) if factors else 0.0,
    }

    report_sections = {
        "deployer": deployer_section,
        "holders": holders_section,
        "first_buyer": first_buyer,
        "bundle": bundle,
        "honeypot": honeypot,
        "liquidity": liquidity,
    }
    deeper = _build_deeper_analysis(report_sections, factors)

    return RiskReport(
        token_address=r.token_address,
        chain=r.chain,
        scanned_at=r.scanned_at,
        tier=r.tier,
        deployer=deployer_section,
        holders=holders_section,
        first_buyer=first_buyer,
        top_25_holders=top_25,
        fresh_wallet_pct=fresh_pct,
        cex_funded_wallet_pct=cex_pct,
        bundle=bundle,
        cluster=cluster_info,
        honeypot=honeypot,
        liquidity=liquidity,
        factors=factors,
        composite_score=composite,
        determination=determination,
        summary=summary,
        deeper_analysis=deeper,
        data_quality=data_quality,
        sources_used=sources_used,
    )


# ═══════════════════════════════════════════════════════════════════════════
# FORMATTER
# ═══════════════════════════════════════════════════════════════════════════


def _fmt_addr(value: Any) -> str:
    if not value:
        return "unknown"
    s = str(value)
    return s if len(s) <= 14 else f"{s[:10]}…{s[-4:]}"


def format_risk_report(report: RiskReport, detailed: bool = False) -> str:
    """Format a ``RiskReport`` into structured, readable text output.

    ``detailed=True`` appends the deeper-level expanded analysis (per-section
    narratives + every factor's explanation) for deep scans.
    """
    lines: list[str] = []
    lines.append("═" * 62)
    lines.append("RUGMUNCH SCANNER — STRUCTURED RISK REPORT")
    lines.append("═" * 62)
    lines.append(f"Token : {_fmt_addr(report.token_address)}  ·  chain {report.chain}")
    if report.scanned_at:
        lines.append(f"Scanned: {report.scanned_at}  ·  tier {report.tier or 'free'}")
    lines.append("")

    # ── Composite + determination ──
    comp = report.composite_score if report.composite_score is not None else "unknown"
    lines.append(f"COMPOSITE RISK SCORE : {comp}/100")
    lines.append(f"DETERMINATION        : {report.determination.upper()}")
    lines.append(
        f"DATA QUALITY         : {report.data_quality.get('factors_known', 0)}/"
        f"{report.data_quality.get('factors_total', 0)} factors known "
        f"({report.data_quality.get('coverage_pct', 0.0)}%)"
    )
    if report.summary:
        lines.append(f"SUMMARY              : {report.summary}")
    lines.append("")

    # ── Deployer ──
    dep = report.deployer
    lines.append("── DEPLOYER ──────────────────────────────────────────────")
    lines.append(f"  Address         : {_fmt_addr(dep.get('address'))}")
    if dep.get("risk_bucket"):
        lines.append(f"  Fingerprint     : {dep.get('risk_bucket')} (risk {dep.get('risk_score')}/100)")
    lines.append(
        f"  Deployments     : {dep.get('total_deployments', 'unknown')}"
        f"  ·  repeat={dep.get('repeat_deployer')}  ·  previously_rugged={dep.get('previously_rugged')}"
    )
    funder = dep.get("funder") or {}
    if funder.get("address"):
        lines.append(
            f"  Funded by       : {_fmt_addr(funder.get('address'))}"
            f"{' (CEX ' + str(funder.get('cex_label')) + ')' if funder.get('is_cex') else ''}"
            f"{' (fresh)' if funder.get('is_fresh') else ''}"
        )
    lines.append("")

    # ── Holders ──
    hol = report.holders
    lines.append("── HOLDERS ───────────────────────────────────────────────")
    lines.append(f"  Total holders   : {hol.get('total_holders', 'unknown')}")
    lines.append(
        f"  Top-10 share    : {hol.get('top_10_pct', 'unknown')}%"
        f"  ·  top-25 share: {hol.get('top_25_pct', 'unknown')}%"
        f"  ·  concentration: {hol.get('concentration', 'unknown')}"
    )
    lines.append(f"  Gini/HHI        : {hol.get('gini', 'unknown')} / {hol.get('hhi', 'unknown')}")
    lines.append(
        f"  Dev/team share  : {hol.get('dev_team_share', 'unknown')}%"
        f"  ·  fresh wallets: {report.fresh_wallet_pct if report.fresh_wallet_pct is not None else 'unknown'}%"
        f"  ·  CEX-funded: {report.cex_funded_wallet_pct if report.cex_funded_wallet_pct is not None else 'unknown'}%"
    )
    lines.append("")

    # ── First buyer ──
    fb = report.first_buyer
    lines.append("── FIRST BUYER ───────────────────────────────────────────")
    lines.append(
        f"  Address         : {_fmt_addr(fb.get('address'))}"
        f"  ·  is_deployer={fb.get('is_deployer')}  ·  in_bundle={fb.get('in_bundle')}"
    )
    if fb.get("block") is not None:
        lines.append(f"  Block/Tx        : {fb.get('block')} / {_fmt_addr(fb.get('tx'))}")
    if fb.get("cohort_size") is not None:
        cohort = f"{fb.get('cohort_size')} wallets" + (
            f" (limit {fb.get('cohort_limit')})" if fb.get("cohort_limit") is not None else ""
        )
        extra = []
        if fb.get("cohort_fresh_pct") is not None:
            extra.append(f"{fb['cohort_fresh_pct']}% fresh")
        if fb.get("cohort_bundled_pct") is not None:
            extra.append(f"{fb['cohort_bundled_pct']}% bundled")
        cohort += f" · {' · '.join(extra)}" if extra else ""
        lines.append(f"  Cohort (own 250-wallet engine): {cohort}")
    lines.append("")

    # ── Top 25 holders ──
    lines.append("── TOP 25 HOLDERS ────────────────────────────────────────")
    if report.top_25_holders:
        for idx, h in enumerate(report.top_25_holders[:25], start=1):
            label = f" [{h.get('label')}]" if h.get("label") else ""
            cluster = f" (cluster {h.get('cluster_id')})" if h.get("cluster_id") else ""
            lines.append(f"  {idx:>2}. {_fmt_addr(h.get('address')):<18} {h.get('share_pct', '?')}%{label}{cluster}")
    else:
        lines.append("  (unavailable)")
    lines.append("")

    # ── Bundle ──
    bun = report.bundle
    lines.append("── BUNDLE / SNIPING ─────────────────────────────────────")
    lines.append(
        f"  Bundled launch  : {bun.get('is_bundled', 'unknown')}  ·  confidence: {bun.get('confidence', 'unknown')}"
    )
    lines.append(
        f"  Launch cluster  : {bun.get('launch_cluster_score', 'unknown')}/100"
        f"  ·  deployer-funded: {bun.get('deployer_funded_pct', 'unknown')}%"
        f"  ·  first-time buyers: {bun.get('first_time_buyer_pct', 'unknown')}%"
    )
    lines.append(f"  Sniper buyers   : {bun.get('sniper_first_buyers', 'unknown')}")
    lines.append("")

    # ── Cluster ──
    cl = report.cluster
    lines.append("── CLUSTER DETECTION (beyond market) ────────────────────")
    lines.append(
        f"  Cluster risk    : {cl.cluster_risk_score if cl.cluster_risk_score is not None else 'unknown'}/100"
        f"  ·  label: {cl.cluster_risk_label}"
    )
    lines.append(
        f"  Funder-tree roots : {len(cl.funder_tree)}"
        f"  ·  shared-deployer nets: {len(cl.shared_deployer_network)}"
        f"  ·  cross-token clusters: {len(cl.cross_token_clusters)}"
    )
    wash_cycles = cl.wash_clusters.get("cycles") or [] if isinstance(cl.wash_clusters, dict) else []
    wash_ratio = cl.wash_clusters.get("cycle_volume_ratio") if isinstance(cl.wash_clusters, dict) else None
    lines.append(
        f"  Cross-token funders: {len(cl.cross_token_funders)}"
        f"  ·  lockstep groups: {len(cl.temporal_clusters)}"
        f"  ·  sniper/bot patterns: {len(cl.sniper_patterns)}"
    )
    lines.append(
        f"  MEV bundles     : {len(cl.mev_bundles)}"
        f"  ·  wash cycles: {len(wash_cycles)}"
        + (f"  ·  cycle volume: {float(wash_ratio) * 100:.1f}%" if isinstance(wash_ratio, (int, float)) else "")
    )
    for c in cl.funder_tree[:5]:
        deployer_flag = " (DEPLOYER-FUNDED)" if c.get("funder_is_deployer") else ""
        lines.append(
            f"    · funder {_fmt_addr(c.get('funder'))} → {c.get('holder_count')} holders, "
            f"{c.get('share_pct')}%{deployer_flag}"
        )
    for c in cl.shared_deployer_network[:3]:
        lines.append(
            f"    · ring: {c.get('launch_count')} launches, {len(c.get('shared_wallets') or [])} shared wallets "
            f"({c.get('deployer', '?')[:10]}…)"
        )
    for c in cl.cross_token_clusters[:3]:
        lines.append(f"    · co-hold: {c.get('members')} wallets together in {_fmt_addr(c.get('token'))}")
    for c in cl.cross_token_funders[:3]:
        lines.append(
            f"    · funder {_fmt_addr(c.get('funder'))} seeded {c.get('launch_count')} other launches "
            f"({c.get('funded_wallet_count')} wallets)"
        )
    for c in cl.temporal_clusters[:3]:
        lines.append(
            f"    · lockstep: {c.get('members')} wallets entered block {c.get('block')} (span {c.get('span_blocks')})"
        )
    for p in cl.sniper_patterns[:3]:
        lines.append(f"    · bot: {p.get('pattern')} ({p.get('members')} wallets)")
    for b in cl.mev_bundles[:3]:
        lines.append(f"    · mev: {b.get('pattern')} block {b.get('block')} ({b.get('members')} wallets)")
    for c in wash_cycles[:3]:
        lines.append(
            f"    · wash: {c.get('cycle_length')}-wallet {c.get('pattern_type')} "
            f"vol {c.get('combined_volume')} conf {c.get('confidence')}"
        )
    if cl.description:
        lines.append(f"  {cl.description}")
    lines.append("")

    # ── Honeypot / contract ──
    hp = report.honeypot
    lines.append("── HONEYPOT + RUG VECTORS ───────────────────────────────")
    lines.append(
        f"  Honeypot        : {hp.get('is_honeypot', 'unknown')}  ·  can_sell: {hp.get('can_sell', 'unknown')}"
    )
    lines.append(f"  Buy/Sell tax    : {hp.get('buy_tax', 'unknown')}% / {hp.get('sell_tax', 'unknown')}%")
    lines.append(
        f"  Mint authority  : {hp.get('mintable', 'unknown')}"
        f"  ·  freeze: {hp.get('freezable', 'unknown')}"
        f"  ·  ownership renounced: {hp.get('ownership_renounced', 'unknown')}"
    )
    if hp.get("warnings"):
        lines.append("  Warnings:")
        for w in hp["warnings"][:5]:
            lines.append(f"    · {w}")
    lines.append("")

    # ── Liquidity ──
    liq = report.liquidity
    lines.append("── LIQUIDITY ────────────────────────────────────────────")
    lines.append(
        f"  LP locked       : {liq.get('locked_pct', 'unknown')}%"
        f"  ·  is_locked: {liq.get('is_locked', 'unknown')}"
        f"  ·  burned: {liq.get('is_burned', 'unknown')}"
    )
    if liq.get("lock_contract"):
        lines.append(f"  Lock contract   : {_fmt_addr(liq.get('lock_contract'))}")
    lines.append(
        f"  Liquidity       : ${liq.get('liquidity_usd', 'unknown'):,.0f}"
        if isinstance(liq.get("liquidity_usd"), (int, float))
        else "  Liquidity       : unknown"
    )
    lines.append("")

    # ── Factors ──
    lines.append("── RISK FACTORS (~25) ───────────────────────────────────")
    for f in report.factors:
        score = f"{f.risk_score:.0f}/100" if f.risk_score is not None else "unknown"
        lines.append(f"  [{f.status.upper():<8}] {f.label:<34} {score:>8}")
    lines.append("")

    if detailed:
        lines.append("── DEEPER-LEVEL ANALYSIS ──────────────────────────────")
        for section, payload in report.deeper_analysis.items():
            lines.append(f"  ► {section.upper()}")
            if payload.get("summary"):
                lines.append(f"      {payload['summary']}")
            for ln in payload.get("factors") or []:
                lines.append(f"      {ln}")
        if report.ai_assisted:
            lines.append("  (analysis AI-assisted)")
        lines.append("")

    lines.append("═" * 62)
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# SCANNER PHASE WIRING
# ═══════════════════════════════════════════════════════════════════════════


async def run_risk_report_phase(r: ScanResult, address: str, chain: str) -> None:
    """Run the RiskReport phase: resolve deployer, run own engines, attach report.

    Best-effort: each engine degrades gracefully (unavailable → the affected
    factors report ``unknown``). Never raises; on total failure the phase is a
    no-op and ``ScanResult.risk_report`` stays ``None``.
    """
    try:
        # ── Resolve deployer (best-effort, several sources) ──
        from app.domains.scanner.unified.sentinel_phase import extract_deployer_from_security

        deployer = extract_deployer_from_security(r.security)
        if deployer is None and isinstance(r.sentinel, dict):
            for bucket in ("free", "premium"):
                mods = r.sentinel.get(bucket)
                if not isinstance(mods, dict):
                    continue
                ca = mods.get("contract_authority")
                if isinstance(ca, dict):
                    deployer = extract_deployer_from_security(ca) or deployer

        # ── Deployer fingerprint (own ClickHouse — repeat/rugged history) ──
        fingerprint: dict[str, Any] | None = None
        if deployer:
            try:
                from app.domains.scanner.engines.deployer_fingerprint import fingerprint_deployer

                fingerprint = await asyncio.wait_for(fingerprint_deployer(deployer, chain), timeout=10)
            except Exception as exc:
                logger.debug("risk_report_fingerprint_failed", error=str(exc)[:120])
                fingerprint = None

        # ── BubbleMaps-style holder clusters (own ClickHouse) ──
        holder_clusters: dict[str, Any] | None = None
        try:
            from app.domains.scanner.engines.holder_clusters import analyze_holder_clusters

            holder_clusters = await asyncio.wait_for(analyze_holder_clusters(address, chain), timeout=15)
        except Exception as exc:
            logger.debug("risk_report_holder_clusters_failed", error=str(exc)[:120])
            holder_clusters = None

        # ── Beyond-market wallet CLUSTER graph (own ClickHouse) ──
        cluster_graph: dict[str, Any] | None = None
        try:
            from app.domains.scanner.engines.risk_cluster import analyze_wallet_clusters

            holders_list: list[str] = []
            if isinstance(holder_clusters, dict) and isinstance(holder_clusters.get("holders"), list):
                holders_list = [
                    str(h.get("address"))
                    for h in holder_clusters["holders"]
                    if isinstance(h, dict) and h.get("address")
                ]
            bundle = r.bundle_analysis if isinstance(r.bundle_analysis, dict) else None
            cluster_graph = await asyncio.wait_for(
                analyze_wallet_clusters(address, chain, deployer=deployer, holders=holders_list, bundle=bundle),
                timeout=12,
            )
        except Exception as exc:
            logger.debug("risk_report_cluster_graph_failed", error=str(exc)[:120])
            cluster_graph = None

        # ── Assemble + attach ──
        report = build_risk_report(
            r,
            deployer_fingerprint=fingerprint,
            holder_clusters=holder_clusters,
            cluster_graph=cluster_graph,
        )
        r.risk_report = report.to_dict()
        if report.data_quality.get("factors_known", 0) > 0:
            r.modules_run.append("risk_report")
            r.enrichment_sources.append("risk_report:structured")
    except Exception as exc:
        logger.debug("risk_report_phase_failed", error=str(exc)[:160])
