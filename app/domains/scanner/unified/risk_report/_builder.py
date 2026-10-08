"""Public builder for the risk_report package.

``build_risk_report`` is a pure aggregation from a ``ScanResult`` (plus the
optional injectable engine payloads). It never raises; every unavailable factor
is marked ``unknown``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.unified.risk_report._composite import _composite_and_determination
from app.domains.scanner.unified.risk_report._deeper import _build_deeper_analysis
from app.domains.scanner.unified.risk_report._extract import (
    _extract_bundle_section,
    _extract_deployer_section,
    _extract_first_buyer_section,
    _extract_holders_section,
    _extract_honeypot_section,
    _extract_liquidity_section,
    _f,
    _i,
)
from app.domains.scanner.unified.risk_report._factors import _build_factors
from app.domains.scanner.unified.risk_report._model import ClusterInfo, RiskFactor, RiskReport

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)


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
