"""SENTINEL deep-forensic scan report.

Split out of ``formatters/_scan_report.py`` during the Batch 14
de-godding pass: the deep scan section (from
``POST /api/v1/x402-tools/sentinel_scan``) plus its standalone wrapper."""

from __future__ import annotations

import contextlib
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
    "format_sentinel_deepscan_section",
    "format_sentinel_scan_report",
]

# --- SENTINEL deep scan report (from POST /api/v1/x402-tools/sentinel_scan) ---


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

    lines: list[str] = [
        "\U0001f6e1\ufe0f <b>AEGIS DEEP FORENSIC SCAN</b>",
        s,
    ]
    if address:
        lines.append(f"<code>{address}</code>")
    lines.append(f"{fmt_chain(chain)}\n")

    # ── Composite risk assessment ──
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
