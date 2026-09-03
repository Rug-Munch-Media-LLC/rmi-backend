"""Scanner-phase wiring for the risk_report package.

``run_risk_report_phase`` is the async scanner-phase delegate: it resolves the
deployer, runs the deployer-fingerprint / holder-cluster / wallet-cluster
engines (best-effort) and attaches ``ScanResult.risk_report``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.domains.scanner.engines._logging import get_logger
from app.domains.scanner.unified.risk_report._builder import build_risk_report

if TYPE_CHECKING:
    from app.domains.scanner.unified.model import ScanResult

logger = get_logger(__name__)

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

        # ── Surface holder concentration on the ScanResult (2026-08-25
        # quality push): the API formatter reads ``r.holder_concentration``
        # directly — thin clients (bot + site) get top-10 %, Gini, HHI and
        # the top holders without digging through the risk report. ──
        if isinstance(holder_clusters, dict) and holder_clusters.get("result") == "scanned":
            hc_metrics = holder_clusters.get("metrics") or {}
            hc_conc = holder_clusters.get("concentration") or {}
            r.holder_concentration = {
                "top10_pct": hc_metrics.get("top10_pct", hc_conc.get("top10_pct")),
                "top3_pct": hc_conc.get("top3_pct"),
                "gini": hc_metrics.get("gini"),
                "hhi": hc_metrics.get("hhi"),
                "class": hc_conc.get("level") or hc_metrics.get("class"),
                "total_holders": hc_metrics.get("total_holders"),
                "clusters_found": len(holder_clusters.get("clusters") or []),
                "top_holders": [
                    {
                        "address": h.get("address"),
                        "pct": h.get("balance_pct"),
                        "label": h.get("label"),
                    }
                    for h in (holder_clusters.get("holders") or [])[:10]
                    if isinstance(h, dict) and h.get("address")
                ],
            }

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
