#!/usr/bin/env python3
"""
RugMunchBot -- /deepscan command: ML + contract-analysis deep scan.

Wraps ``app.deep_scan.deep_scan_address`` — the platform's deep-scan
orchestrator that composes fraud-GNN / pyod / pygod anomaly scores, bytecode
decompilation (heimdall), sell simulation (pyrevm) and Slither static
analysis into one :class:`DeepScanReport`.

Conventions follow ``handlers/osint.py`` (reply with a status message, then
``edit_text`` the result; ``back_kb`` on usage/errors). The scan is blocking
(network RPC + ML + EVM simulation) so it runs via ``asyncio.to_thread`` to
keep the event loop responsive. Graceful degradation: a missing module/dep or
any failure becomes a helpful message instead of a crash.
"""

from __future__ import annotations

import asyncio
import html
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.formatters import footer_links, thin_sep
from app.domains.telegram.rugmunchbot.keyboards import back_kb
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.deepscan")

# ─── Tunables ────────────────────────────────────────────────────────

#: Max findings rendered inline; the rest collapse to "+N more…".
_MAX_FINDINGS = 5

#: overall_risk / risk_level -> emoji.
_RISK_EMOJI: dict[str, str] = {
    "low": "\U0001f7e2",
    "medium": "\U0001f7e1",
    "high": "\U0001f7e0",
    "critical": "\U0001f534",
}

#: Finding level -> emoji.
_FINDING_EMOJI: dict[str, str] = {
    "error": "\u274c",
    "warning": "\u26a0\ufe0f",
    "info": "\u2139\ufe0f",
}

#: Inline flag suffix for anomaly findings (kept as a constant so f-string
#: expressions stay Python 3.11 compatible — no \u escapes inside braces).
_ANOMALY_FLAG = " \u26a0\ufe0f anomaly"
_SKIPPED_NO_GRAPH = "skipped (no transfer graph)"

#: Severity rank for sorting findings (higher = shown first).
_LEVEL_RANK: dict[str, int] = {"error": 3, "warning": 2, "info": 1}


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


def _format_deepscan_report(report: Any) -> str:
    """Render a DeepScanReport as a compact HTML message.

    Sections: overall risk header, ML scores (fraud_gnn / pyod / pygod),
    decompiled ABI summary, sell-sim flags, then top findings.
    """
    emoji = _RISK_EMOJI.get(str(report.overall_risk).lower(), "\u26aa")
    parts = [
        f"\U0001f52c <b>Deep ML Scan</b> {emoji} <b>{html.escape(str(report.overall_risk).upper())}</b>",
        thin_sep(),
        f"<code>{html.escape(str(report.address))}</code>",
        f"\u26d3\ufe0f Chain: <b>{html.escape(str(report.chain))}</b>",
        "",
    ]

    scores = report.risk_scores or {}

    # ── ML scores ───────────────────────────────────────────────────
    parts.append("\U0001f4ca <b>ML Scores</b>")
    fraud = scores.get("fraud_gnn") or {}
    if fraud.get("error"):
        parts.append(f"\U0001f9e0 fraud_gnn: <i>{html.escape(str(fraud['error'])[:60])}</i>")
    else:
        prob = _fmt_score(fraud.get("fraud_probability"))
        model = html.escape(str(fraud.get("model") or "unknown"))
        flag = f"{_ANOMALY_FLAG} fraud" if fraud.get("is_fraud") else ""
        parts.append(f"\U0001f9e0 fraud_gnn: <b>{prob}</b> ({model}){flag}")

    pyod = scores.get("pyod") or {}
    if pyod.get("error"):
        parts.append(f"\U0001f4c8 pyod: <i>{html.escape(str(pyod['error'])[:60])}</i>")
    else:
        parts.append(
            f"\U0001f4c8 pyod: <b>{_fmt_score(pyod.get('anomaly_score'))}</b> "
            f"({html.escape(str(pyod.get('model_type') or 'unknown'))})"
            f"{_ANOMALY_FLAG if pyod.get('is_anomaly') else ''}"
        )

    pygod = scores.get("pygod") or {}
    if pygod.get("model_type") == "skipped":
        parts.append(f"\U0001f578\ufe0f pygod: <i>{_SKIPPED_NO_GRAPH}</i>")
    elif pygod.get("error"):
        parts.append(f"\U0001f578\ufe0f pygod: <i>{html.escape(str(pygod['error'])[:60])}</i>")
    else:
        parts.append(
            f"\U0001f578\ufe0f pygod: <b>{_fmt_score(pygod.get('anomaly_score'))}</b>"
            f"{_ANOMALY_FLAG if pygod.get('is_anomaly') else ''}"
        )

    # ── Decompiled ABI ──────────────────────────────────────────────
    decompiled = report.decompiled or {}
    parts.append("")
    if decompiled.get("success"):
        n_funcs = len(decompiled.get("functions") or [])
        parts.append(f"\U0001f9e9 <b>Decompiled ABI:</b> {n_funcs} function(s) from bytecode")
    else:
        err = decompiled.get("decompile_error") or "unavailable"
        parts.append(f"\U0001f9e9 <b>Decompiled ABI:</b> <i>{html.escape(str(err)[:80])}</i>")

    # ── Sell simulation flags ───────────────────────────────────────
    sell = report.sell_sim or {}
    parts.append("")
    if sell.get("simulation_success"):
        flags: list[str] = []
        if sell.get("can_sell") and sell.get("sell_to_pair_reverted"):
            flags.append("\U0001f345 honeypot")
        if sell.get("blacklist_hit"):
            flags.append("\U0001f6ab blacklist")
        if sell.get("anti_whale_hit"):
            flags.append("\U0001f40b anti-whale")
        risk = html.escape(str(sell.get("risk") or "unknown"))
        parts.append(
            f"\U0001f4b8 <b>Sell Sim:</b> risk={risk} \u00b7 "
            f"tax {_fmt_tax(sell.get('tax_pct'))}" + (f" \u00b7 {' '.join(flags)}" if flags else "")
        )
    else:
        warn = (sell.get("warnings") or ["unavailable"])[0]
        parts.append(f"\U0001f4b8 <b>Sell Sim:</b> <i>{html.escape(str(warn)[:80])}</i>")

    # ── Top findings ────────────────────────────────────────────────
    findings = list(report.findings or [])
    findings.sort(key=lambda f: _LEVEL_RANK.get(str(f.get("level")), 0), reverse=True)
    parts.append("")
    if not findings:
        parts.append("<i>No findings recorded.</i>")
    else:
        parts.append(f"\U0001f50e <b>Findings</b> (top {_MAX_FINDINGS})")
        for f in findings[:_MAX_FINDINGS]:
            level = str(f.get("level") or "info")
            emoji_f = _FINDING_EMOJI.get(level, "\u2022")
            msg = html.escape(str(f.get("message") or ""))
            module = html.escape(str(f.get("module") or ""))
            parts.append(f"{emoji_f} {msg} <i>({module})</i>")
        extra = len(findings) - _MAX_FINDINGS
        if extra > 0:
            parts.append(f"<i>\u2026and {extra} more</i>")

    parts.append(footer_links())
    return "\n".join(parts)


async def cmd_deepscan(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/deepscan <address> [chain] — ML + contract-analysis deep scan.

    Runs fraud-GNN / pyod / pygod anomaly scores, bytecode decompilation,
    sell simulation and (when source is available) Slither analysis.
    """
    user = update.effective_user
    if user is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    if not ctx.args:
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\U0001f52c <b>Deep ML Scan</b>\n\n"
            "Usage: /deepscan <code>address</code> [chain]\n\n"
            "Examples:\n"
            "  /deepscan <code>0xBE0eB53FC46b790099138e3d32C721856d41e865</code>\n"
            "  /deepscan <code>EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v</code> solana\n\n"
            "ML anomaly scores (fraud-GNN, pyod, pygod), decompiled ABI, "
            "sell-simulation honeypot/tax/blacklist flags and top findings.\n\n"
            "\u23f3 Can take 30-90s (RPC + ML + EVM simulation).",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    address = ctx.args[0].strip()
    if len(address) < 20 or any(c.isspace() for c in address):
        await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
            "\u26a0\ufe0f <b>Invalid address</b>\n\nPass a full EVM <code>0x\u2026</code> or Solana base58 address.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return
    chain = ctx.args[1].strip().lower() if len(ctx.args) > 1 else "auto"

    msg = await update.message.reply_text(  # type: ignore[union-attr]  # PTB None-able fields
        f"\U0001f52c <b>Deep scan started...</b>\n<code>{html.escape(address)}</code>\n\n"
        "\u23f3 Running ML + contract analysis (30-90s)...",
        parse_mode=ParseMode.HTML,
    )
    try:
        from app.deep_scan import deep_scan_address
    except (ImportError, AttributeError) as exc:
        logger.warning("deep_scan module unavailable: %s", exc)
        await msg.edit_text(
            "\u26a0\ufe0f <b>Deep scan unavailable</b>\n\n"
            "The deep-scan engine is not installed on this server. "
            "Check that the ML/contract modules (heimdall-py, pyrevm, pyod) are provisioned.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
        return

    try:
        report = await asyncio.to_thread(deep_scan_address, address, chain=chain)
        await msg.edit_text(
            _format_deepscan_report(report),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as exc:  # deep scan is best-effort; never crash the handler
        logger.warning("deepscan failed for %s: %s", address, exc)
        await msg.edit_text(
            "\u274c <b>Deep Scan Failed</b>\n\n"
            f"<code>{html.escape(address)}</code>\n\n"
            "Could not complete the deep scan. Please try again later.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
