"""Text formatter for the risk_report package.

``format_risk_report`` renders a ``RiskReport`` into structured, readable text
output with per-factor explanations and (on ``detailed=True``) the expanded
deeper-level analysis.
"""

from __future__ import annotations

from typing import Any

from app.domains.scanner.unified.risk_report._model import _MIN_KNOWN_FOR_DETERMINATION, RiskReport


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
    # Honest-output guard: a composite/verdict built from too few known factors
    # ("0/100 OK · 1/30 factors known") is self-contradictory and misleads the
    # reader. OMIT the verdict block unless enough factors are known to declare
    # a determination, or a decisive scam (confirmed honeypot) is present.
    dq = report.data_quality or {}
    try:
        _factors_known = int(dq.get("factors_known", 0))
    except (TypeError, ValueError):
        _factors_known = 0
    try:
        _factors_total = int(dq.get("factors_total", 0))
    except (TypeError, ValueError):
        _factors_total = 0
    _verdict_solid = _factors_known >= _MIN_KNOWN_FOR_DETERMINATION or report.determination == "scam"
    if _verdict_solid:
        comp = report.composite_score if report.composite_score is not None else "unknown"
        lines.append(f"COMPOSITE RISK SCORE : {comp}/100")
        lines.append(f"DETERMINATION        : {report.determination.upper()}")
        lines.append(
            f"DATA QUALITY         : {dq.get('factors_known', 0)}/"
            f"{dq.get('factors_total', 0)} factors known "
            f"({dq.get('coverage_pct', 0.0)}%)"
        )
        if report.summary:
            lines.append(f"SUMMARY              : {report.summary}")
    else:
        lines.append("⚠  INSUFFICIENT DATA — SCAN PENDING")
        lines.append(
            f"    Only {_factors_known}/{_factors_total or '?'} factors known; a composite verdict "
            f"requires ≥ {_MIN_KNOWN_FOR_DETERMINATION}. Re-scan or run a deeper tier."
        )
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
    _hp = hp.get("is_honeypot")
    if _hp is True:
        hp_label = "🚨 HONEYPOT"
    elif _hp is False:
        hp_label = "Not a honeypot"
    else:
        hp_label = "unknown"
    lines.append(f"  Honeypot        : {hp_label}  ·  can_sell: {hp.get('can_sell', 'unknown')}")
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
