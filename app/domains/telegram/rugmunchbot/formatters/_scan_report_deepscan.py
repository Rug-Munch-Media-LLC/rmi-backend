"""SENTINEL deep-forensic scan report.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: the deep scan section (from
``POST /api/v1/x402-tools/sentinel_scan``) plus its standalone wrapper.

Canonical advanced layout (Batch 2026-08-16): every deep-scan report shares
the scan-report skeleton — one-line verdict up top, key metrics as a tight
bold table, evidence bullets, and a short "what to watch" line. The ML deep
scan (``handlers/deepscan.py``) delegates to ``format_deep_ml_scan_report``
here so there is exactly ONE deep-scan formatter path."""

from __future__ import annotations

import contextlib
import html
from typing import Any

from app.domains.telegram.rugmunchbot.config import fmt_chain
from app.domains.telegram.rugmunchbot.formatters._base import footer_links, sep, thin_sep
from app.domains.telegram.rugmunchbot.formatters._scan_report_common import _clip, _collapse_blank_lines
from app.domains.telegram.rugmunchbot.formatters._scan_report_risk import _format_risk_signals_section
from app.domains.telegram.rugmunchbot.formatters._sentinel_helpers import (
    SENTINEL_MODULE_EMOJI,
    _risk_level_emoji,
    _sentinel_recommendations,
)

__all__ = [
    "format_deep_ml_scan_report",
    "format_sentinel_deepscan_section",
    "format_sentinel_scan_report",
]

# --- SENTINEL deep scan report (from POST /api/v1/x402-tools/sentinel_scan) ---

#: Overall-risk level → emoji for the ML deep scan.
_RISK_EMOJI: dict[str, str] = {
    "low": "\U0001f7e2",  # 🟢
    "medium": "\U0001f7e1",  # 🟠
    "high": "\U0001f7e0",  # 🟡
    "critical": "\U0001f534",  # 🔴
}

#: Finding level → emoji.
_FINDING_EMOJI: dict[str, str] = {
    "error": "\u274c",
    "warning": "\u26a0\ufe0f",
    "info": "\u2139\ufe0f",
}

#: Severity rank for sorting findings (higher = shown first).
_LEVEL_RANK: dict[str, int] = {"error": 3, "warning": 2, "info": 1}

#: Max findings rendered inline; the rest collapse to "+N more…".
_MAX_FINDINGS = 5


def _fmt_score(value: Any) -> str:
    """Format a 0..1 probability/score as a percentage string."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if num < 0 or num > 1:
        return f"{num:.2f}"
    return f"{num * 100:.1f}%"


def _fmt_tax(value: Any) -> str:
    """Format a tax percent (already 0..100 scale) or 'n/a'."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "n/a"
    return f"{num:.1f}%"


def _ml_verdict_sentence(overall: str, sell: dict[str, Any]) -> str:
    """One-line plain-language verdict sentence for the ML deep scan."""
    level = str(overall or "").lower()
    if level == "critical":
        return "critical ML signals \u2014 avoid; treat as a probable scam"
    if level == "high":
        return "multiple anomaly signals \u2014 high caution before any buy"
    if level == "medium":
        return "elevated ML risk \u2014 verify everything, size small"
    return "no major ML anomaly signals \u2014 normal caution applies"


def _ml_what_to_watch(overall: str, findings: list[dict[str, Any]]) -> str:
    """Short actionable "what to watch" line for the ML deep scan."""
    level = str(overall or "").lower()
    joined = " ".join(str(f.get("message") or "") for f in findings).lower()
    if "honeypot" in joined:
        return "Verify you can actually SELL before buying \u2014 honeypot signal from sell simulation."
    if "blacklist" in joined:
        return "Owner can blacklist wallets \u2014 holders can be frozen out mid-trade."
    if "dangerous function" in joined:
        return "Decompiled ABI exposes dangerous functions \u2014 treat the contract as hostile."
    if level == "critical":
        return "Critical ML verdict \u2014 avoid this token entirely."
    if level == "high":
        return "Multiple anomaly signals \u2014 watch for sudden exit liquidity pulls."
    if level == "medium":
        return "Elevated risk \u2014 watch taxes and sell liquidity before entering."
    return "Standard caution \u2014 size in small and watch sell pressure."


def format_deep_ml_scan_report(report: Any) -> str:
    """Canonical ML deep-scan report (``/deepscan`` — fraud-GNN/pyod/pygod).

    Renders a ``app.deep_scan.DeepScanReport`` using the shared scan-report
    skeleton: one-line verdict up top, a tight ML metrics table, sell-sim +
    findings as evidence bullets, and a "what to watch" line. HTML-safe —
    every data-derived string is escaped.
    """
    s = sep()
    d = thin_sep()
    overall = str(getattr(report, "overall_risk", "unknown") or "unknown").lower()
    scores = dict(getattr(report, "risk_scores", None) or {})
    sell = dict(getattr(report, "sell_sim", None) or {})
    decompiled = dict(getattr(report, "decompiled", None) or {})
    findings = list(getattr(report, "findings", None) or [])

    emoji = _RISK_EMOJI.get(overall, "\u26aa")
    lines: list[str] = [
        "\U0001f52c <b>Deep ML Scan</b>",
        s,
        f"<code>{html.escape(str(getattr(report, 'address', '')))}</code>",
        f"\u26d3\ufe0f Chain: <b>{html.escape(str(getattr(report, 'chain', '')))}</b>",
        "",
        # ── 1. One-line verdict up top ──
        f"{emoji} <b>Verdict: {html.escape(overall.upper())}</b> \u2014 {_ml_verdict_sentence(overall, sell)}",
    ]

    # ── 2. Key metrics as a tight bold table (ML-specific) ──
    fraud = scores.get("fraud_gnn") or {}
    pyod = scores.get("pyod") or {}
    pygod = scores.get("pygod") or {}

    def _score_row(label: str, entry: dict[str, Any], model_key: str) -> str:
        if entry.get("error"):
            return f"  {label:<16} <i>{html.escape(str(entry['error'])[:60])}</i>"
        raw_score = entry.get("fraud_probability") if "fraud_probability" in entry else entry.get("anomaly_score")
        score = _fmt_score(raw_score)
        model = html.escape(str(entry.get(model_key) or "unknown"))
        flag = " \u26a0\ufe0f anomaly" if entry.get("is_anomaly") or entry.get("is_fraud") else ""
        return f"  {label:<16} <b>{score}</b> ({model}){flag}"

    metrics: list[str] = []
    if fraud or fraud.get("error"):
        metrics.append(_score_row("fraud_gnn", fraud, "model"))
    if pyod or pyod.get("error"):
        metrics.append(_score_row("pyod", pyod, "model_type"))
    if pygod.get("model_type") == "skipped":
        metrics.append("  pygod            <i>skipped (no transfer graph)</i>")
    elif pygod or pygod.get("error"):
        metrics.append(_score_row("pygod", pygod, "model_type"))
    metrics.append(f"  Sell-Sim Tax     <b>{_fmt_tax(sell.get('tax_pct'))}</b>")
    if decompiled.get("success"):
        metrics.append(f"  ABI Functions    <b>{len(decompiled.get('functions') or [])}</b>")
    metrics.append(f"  Findings         <b>{len(findings)}</b>")
    lines.extend(["", "\U0001f4ca <b>Key Metrics</b>", d, *metrics])

    # ── 3. Evidence bullets ──
    # Sell-sim flags (honeypot / blacklist / anti-whale) — hard evidence.
    if sell.get("simulation_success"):
        flags: list[str] = []
        if sell.get("can_sell") and sell.get("sell_to_pair_reverted"):
            flags.append("\U0001f345 honeypot")
        if sell.get("blacklist_hit"):
            flags.append("\U0001f6ab blacklist")
        if sell.get("anti_whale_hit"):
            flags.append("\U0001f40b anti-whale")
        risk = html.escape(str(sell.get("risk") or "unknown"))
        lines.extend(
            [
                "",
                "\U0001f4b8 <b>Sell Simulation</b>",
                d,
                f"  \U0001f504 risk={risk} \u00b7 tax {_fmt_tax(sell.get('tax_pct'))}"
                + (f" \u00b7 {' '.join(flags)}" if flags else ""),
            ]
        )
    else:
        warn = (sell.get("warnings") or ["unavailable"])[0]
        lines.extend(["", "\U0001f4b8 <b>Sell Simulation</b>", d, f"  <i>{html.escape(str(warn)[:80])}</i>"])

    # Decompiled ABI summary.
    if decompiled.get("success"):
        lines.append(
            f"  \U0001f9e9 Decompiled ABI: <b>{len(decompiled.get('functions') or [])}</b> function(s) from bytecode"
        )
    else:
        err = decompiled.get("decompile_error") or "unavailable"
        lines.append(f"  \U0001f9e9 Decompiled ABI: <i>{html.escape(str(err)[:80])}</i>")

    # Findings — evidence bullets, worst first.
    sorted_findings = sorted(findings, key=lambda f: _LEVEL_RANK.get(str(f.get("level")), 0), reverse=True)
    lines.extend(["", "\U0001f50e <b>Findings</b>", d])
    if not sorted_findings:
        lines.append("  \u2705 No findings recorded")
    else:
        for f in sorted_findings[:_MAX_FINDINGS]:
            level = str(f.get("level") or "info")
            emoji_f = _FINDING_EMOJI.get(level, "\u2022")
            msg = html.escape(str(f.get("message") or ""))
            module = html.escape(str(f.get("module") or ""))
            lines.append(f"  {emoji_f} {msg} <i>({module})</i>")
        extra = len(sorted_findings) - _MAX_FINDINGS
        if extra > 0:
            lines.append(f"  <i>\u2026and {extra} more</i>")

    # ── 4. What to watch ──
    lines.extend(["", f"\U0001f440 <b>Watch:</b> {_ml_what_to_watch(overall, sorted_findings)}"])
    lines.append(footer_links())
    return _collapse_blank_lines("\n".join(lines))


def _sentinel_watch_line(red_flags: list[Any], level: str) -> str:
    """Short actionable "what to watch" line for the SENTINEL report."""
    joined = " ".join(str(f).lower() for f in red_flags)
    if "honeypot" in joined:
        return "Verify you can actually SELL before buying \u2014 honeypot signal."
    if "bundle" in joined:
        return "Volume & concentration may be fabricated \u2014 bundle activity."
    if "wash" in joined:
        return "Volume may be fake \u2014 wash trading suspected."
    lvl = str(level or "").lower()
    if lvl in ("critical", "high"):
        return "Avoid this token \u2014 critical risk signals detected."
    if lvl in ("medium", "moderate"):
        return "Proceed with extreme caution \u2014 elevated risk."
    return "Low risk \u2014 standard DeFi caution still applies."


def format_sentinel_deepscan_section(data: dict[str, Any]) -> str:
    """Render the SENTINEL deep-forensic section.

    Consumes the raw response from ``POST /api/v1/x402-tools/sentinel_scan``
    (SentinelReport serialized): composite risk score, per-module risk
    breakdown, red flags, and per-module detail (holder HHI, LP lock,
    contract authority, deployer reputation). Used by /deepscan (appended to
    the base v2 scan report) and /sentinelscan (standalone).
    """
    s = sep()
    d = thin_sep()
    address = data.get("address") or data.get("token_address") or ""
    chain = data.get("chain") or "unknown"
    try:
        risk = float(data.get("composite_risk_score") or 0)
    except (TypeError, ValueError):
        risk = 0.0
    level = str(data.get("risk_level") or "unknown")
    red_flags = data.get("red_flags") or []
    module_scores = data.get("module_risk_scores") or {}
    holder = data.get("holder_analysis") or {}
    liq = data.get("liquidity_verification") or {}
    dev = data.get("dev_reputation") or {}
    authority = data.get("contract_authority") or {}
    bundle = data.get("bundle_detection") or {}
    exchange = data.get("exchange_funding") or {}

    lines: list[str] = [
        "\U0001f6e1\ufe0f <b>AEGIS DEEP FORENSIC SCAN</b>",
        s,
    ]
    if address:
        lines.append(f"<code>{address}</code>")
    lines.append(f"{fmt_chain(chain)}")
    # ── 1. One-line verdict up top (canonical layout) ──
    lines.append(
        f"{_risk_level_emoji(level)} <b>Verdict: {html.escape(level.upper())}</b> \u2014 Composite {risk:.0f}/100"
    )
    lines.append("")

    # ── Risk Assessment ──
    lines.append("\U0001f3af <b>Risk Assessment</b>")
    lines.append(d)
    lines.append(f"{_risk_level_emoji(level)} Composite Risk: <b>{risk:.0f}/100</b> ({level.upper()})")
    lines.append("")

    # ── Consolidated risk signals (scan_enrich output, when present) ──
    risk_lines = _format_risk_signals_section(data)
    if risk_lines:
        lines.extend(risk_lines)
        lines.append("")

    # ── Key findings (red flags) ──
    if red_flags:
        lines.append("\U0001f6a8 <b>Key Findings</b>")
        lines.append(d)
        for flag in red_flags[:8]:
            lines.append(f"  \U0001f534 {_clip(flag, 160)}")
        lines.append("")
    else:
        lines.append("\U0001f7e2 <b>No critical findings</b>")
        lines.append("")

    # ── Module risk breakdown ──
    if module_scores:
        lines.append("\U0001f4ca <b>Module Risk Breakdown</b>")
        lines.append(d)
        ranked = sorted(
            module_scores.items(),
            key=lambda kv: float(kv[1] or 0),
            reverse=True,
        )
        for mod, score in ranked[:10]:
            try:
                mscore = float(score or 0)
            except (TypeError, ValueError):
                mscore = 0.0
            memoji = SENTINEL_MODULE_EMOJI.get(mod, "\U0001f539")  # 🔹
            filled = max(0, min(10, int(mscore / 10)))
            mbar = "\u2b1b" * filled + "\u2b1c" * (10 - filled)
            lines.append(f"  {memoji} {mod.replace('_', ' ').title()}: {mscore:.0f}/100")
            lines.append(f"     <code>{mbar}</code>")
        lines.append("")

    # ── Holder analysis ──
    if holder:
        lines.append("\U0001f465 <b>Holder Analysis</b>")
        lines.append(d)
        hhi = holder.get("hhi")
        if hhi is not None:
            try:
                hhi_f = float(hhi)
            except (TypeError, ValueError):
                hhi_f = 0.0
            hhi_emoji = (
                "\U0001f534" if str(holder.get("risk_level") or "").lower() in ("high", "critical") else "\U0001f7e2"
            )
            lines.append(f"  {hhi_emoji} HHI Concentration: <b>{hhi_f:.0f}</b> ({holder.get('risk_level', 'unknown')})")
        if holder.get("total_holders") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f465 Total Holders: <b>{int(holder['total_holders']):,}</b>")
        if holder.get("top_10_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f40b Top-10 Supply: <b>{float(holder['top_10_percentage']):.0f}%</b>")
        if holder.get("top_20_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f40b Top-20 Supply: <b>{float(holder['top_20_percentage']):.0f}%</b>")
        if holder.get("exchange_funded_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f3e6 Exchange-Funded: <b>{float(holder['exchange_funded_percentage']):.0f}%</b>")
        if holder.get("new_wallets_percentage") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f195 New Wallets (<7d): <b>{float(holder['new_wallets_percentage']):.0f}%</b>")
        # One-line distribution verdict (concentrated vs healthy).
        top10_raw = holder.get("top_10_percentage")
        hhi_raw = holder.get("hhi")
        dist_emoji: str | None = None
        dist_word: str | None = None
        if top10_raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                top10_f = float(top10_raw)
                if top10_f >= 50:
                    dist_emoji, dist_word = "\U0001f534", "CONCENTRATED"
                elif top10_f >= 30:
                    dist_emoji, dist_word = "\U0001f7e1", "MODERATE"
                else:
                    dist_emoji, dist_word = "\U0001f7e2", "HEALTHY"
        if dist_emoji is None and hhi_raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                hhi_f = float(hhi_raw)
                if hhi_f >= 3000:
                    dist_emoji, dist_word = "\U0001f534", "CONCENTRATED"
                elif hhi_f >= 1500:
                    dist_emoji, dist_word = "\U0001f7e1", "MODERATE"
                else:
                    dist_emoji, dist_word = "\U0001f7e2", "HEALTHY"
        if dist_emoji is not None and dist_word is not None:
            lines.append(f"  {dist_emoji} Distribution verdict: <b>{dist_word}</b>")
        for w in (holder.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── LP analysis ──
    if liq:
        lines.append("\U0001f510 <b>LP Analysis</b>")
        lines.append(d)
        locked = liq.get("total_locked_percentage")
        if locked is not None:
            try:
                locked_f = float(locked)
            except (TypeError, ValueError):
                locked_f = 0.0
            lock_emoji = "\U0001f7e2" if locked_f >= 80 else "\U0001f7e1" if locked_f > 0 else "\U0001f534"
            lines.append(f"  {lock_emoji} LP Locked: <b>{locked_f:.0f}%</b>")
        if liq.get("lp_burned"):
            lines.append("  \U0001f525 LP tokens burned")
        if liq.get("fake_lock_detected"):
            lines.append("  \U0001f534 Fake lock detected")
        for w in (liq.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Contract authority ──
    if authority:
        lines.append("\U0001f512 <b>Contract Authority</b>")
        lines.append(d)
        ca_level = str(authority.get("risk_level") or "LOW").upper()
        lines.append(f"  {_risk_level_emoji(ca_level)} Authority Risk: <b>{ca_level}</b>")
        for w in (authority.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Bundle detection (SENTINEL bundle_detection module) ──
    if bundle:
        lines.append("\U0001f4e6 <b>Bundle Detection</b>")
        lines.append(d)
        bundle_detected = bool(
            bundle.get("deployer_funded")
            or bundle.get("same_block_snipers")
            or int(bundle.get("jito_bundles_detected") or 0) > 0
            or str(bundle.get("risk_level") or "").upper() in ("HIGH", "CRITICAL")
        )
        if bundle_detected:
            deployer_wallets = bundle.get("deployer_funded") or []
            snipers = bundle.get("same_block_snipers") or []
            n_wallets = len(deployer_wallets) + len(snipers)
            try:
                bundled_pct = float(bundle.get("total_bundled_percentage") or 0)
            except (TypeError, ValueError):
                bundled_pct = 0.0
            b_level = str(bundle.get("risk_level") or "UNKNOWN").upper()
            lines.append(f"  \U0001f534 Bundle detected: <b>{n_wallets}</b> wallets \u00b7 {bundled_pct:.0f}% bundled")
            lines.append(f"     Risk: <b>{b_level}</b>")
            if snipers:
                lines.append(f"     \U0001f3af Same-block snipers: <b>{len(snipers)}</b>")
            if deployer_wallets:
                lines.append(f"     \U0001f468\u200d\U0001f4bb Deployer-funded wallets: <b>{len(deployer_wallets)}</b>")
            if int(bundle.get("jito_bundles_detected") or 0) > 0:
                lines.append(f"     \u26a1 Jito bundles: <b>{int(bundle['jito_bundles_detected'])}</b>")
        else:
            lines.append("  \U0001f7e2 No bundle detected")
        for w in (bundle.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Exchange funding (SENTINEL exchange_funding module) ──
    if exchange:
        lines.append("\U0001f3e6 <b>Exchange Funding</b>")
        lines.append(d)
        try:
            cex_pct = float(exchange.get("cex_funded_percentage") or 0)
        except (TypeError, ValueError):
            cex_pct = 0.0
        try:
            inst_pct = float(exchange.get("instant_buy_percentage") or 0)
        except (TypeError, ValueError):
            inst_pct = 0.0
        cex_count = int(exchange.get("cex_funded_count") or 0)
        total = int(exchange.get("total_buyers") or 0)
        cex_emoji = "\U0001f534" if cex_pct > 50 else "\U0001f7e1" if cex_pct > 20 else "\U0001f7e2"
        lines.append(f"  {cex_emoji} CEX-funded: <b>{cex_pct:.0f}%</b> ({cex_count}/{total} buyers)")
        if inst_pct > 0:
            lines.append(f"  \u26a1 Instant-buy (fund→buy <60s): <b>{inst_pct:.0f}%</b>")
        breakdown = exchange.get("exchange_breakdown")
        if isinstance(breakdown, dict) and breakdown:
            top = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)[:4]
            lines.append("  \U0001f3e6 " + " \u00b7 ".join(f"{k}: {v}" for k, v in top))
        e_level = str(exchange.get("risk_level") or "UNKNOWN").upper()
        lines.append(f"  {_risk_level_emoji(e_level)} Funding verdict: <b>{e_level}</b>")
        for w in (exchange.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Contract Creation Analysis ──
    creation = data.get("contract_creation") or {}
    if creation:
        lines.append("\U0001f4be <b>Contract Creation Analysis</b>")
        lines.append(d)
        creator = creation.get("creator_address")
        if creator:
            lines.append(f"  \U0001f464 Creator: <code>{creator}</code>")
        block = creation.get("block_number")
        if block:
            lines.append(f"  \U0001f4c5 Block: <b>{block:,}</b>")
        tx_hash = creation.get("tx_hash")
        if tx_hash:
            lines.append(f"  \U0001f517 Tx: <code>{tx_hash}</code>")
        first_interactors = creation.get("first_interactors") or []
        if first_interactors:
            lines.append(f"  \U0001f9e0 First Interactors ({len(first_interactors)}):")
            for i, w in enumerate(first_interactors[:5], 1):
                addr = w.get("address", "")
                label = w.get("label", "")
                fresh = w.get("is_fresh", False)
                fresh_badge = " 🆕" if fresh else ""
                lines.append(f"    {i}. <code>{addr}</code>{fresh_badge} {label}")
        fresh_pct = creation.get("fresh_wallet_percentage")
        if fresh_pct is not None:
            try:
                fpct = float(fresh_pct)
            except (TypeError, ValueError):
                fpct = 0.0
            fresh_emoji = "\U0001f534" if fpct > 50 else "\U0001f7e1" if fpct > 20 else "\U0001f7e2"
            lines.append(f"  {fresh_emoji} Fresh Wallets at Launch: <b>{fpct:.0f}%</b>")
        deployer_funded_pct = creation.get("deployer_funded_percentage")
        if deployer_funded_pct is not None:
            try:
                dfpct = float(deployer_funded_pct)
            except (TypeError, ValueError):
                dfpct = 0.0
            df_emoji = "\U0001f534" if dfpct > 30 else "\U0001f7e1" if dfpct > 10 else "\U0001f7e2"
            lines.append(f"  {df_emoji} Deployer-Funded at Launch: <b>{dfpct:.0f}%</b>")
        lines.append("")

    # ── Deployer reputation ──
    if dev:
        lines.append("\U0001f468\u200d\U0001f4bb <b>Deployer Reputation</b>")
        lines.append(d)
        rep_score = dev.get("reputation_score")
        rep_level = str(dev.get("reputation_level") or "unknown")
        if rep_score is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  {_risk_level_emoji(rep_level)} Reputation: <b>{int(rep_score)}/100</b> ({rep_level.title()})"
                )
        if dev.get("total_launches") is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(
                    f"  \U0001f4e6 Launches: <b>{int(dev['total_launches'])}</b> | "
                    f"Rugged: <b>{int(dev.get('rugged_count') or 0)}</b>"
                )
        rug_rate = dev.get("rug_rate")
        if rug_rate is not None:
            with contextlib.suppress(TypeError, ValueError):
                lines.append(f"  \U0001f4a5 Rug Rate: <b>{float(rug_rate) * 100:.0f}%</b>")
        for w in (dev.get("warnings") or [])[:3]:
            lines.append(f"  \u26a0\ufe0f {_clip(w, 120)}")
        lines.append("")

    # ── Recommendations ──
    lines.append("\U0001f4a1 <b>Recommendations</b>")
    lines.append(d)
    lines.extend(f"  {rec}" for rec in _sentinel_recommendations(risk, level, red_flags))
    lines.append("")
    # ── 4. What to watch (short actionable line) ──
    lines.append(f"\U0001f440 <b>Watch:</b> {_sentinel_watch_line(red_flags, level)}")
    lines.append("")
    lines.append(
        "\u26a0\ufe0f <i>Composite risk is 0-100 (higher = riskier). "
        "AEGIS data is forensic evidence \u2014 always DYOR.</i>"
    )
    _addr = data.get("address", data.get("token", ""))
    _chain = data.get("chain", "solana")
    lines.append(footer_links(_addr, _chain))
    return _collapse_blank_lines("\n".join(lines))


def format_sentinel_scan_report(data: dict[str, Any]) -> str:
    """Standalone /sentinelscan report — SENTINEL deep scan only."""
    return format_sentinel_deepscan_section(data)
