"""Slash commands for the control surface (/units, /gates, /dataops).

Single canonical registration point for the three commands: ``control_handlers``
owns only the ``ctl_*`` taps. Every screen here is read-only — mutations stay
behind the confirm gate in ``control_actions``.
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import CommandHandler

from app.domains.telegram.adminbot import control_ops
from app.domains.telegram.adminbot.admin_ops import _run_subprocess
from app.domains.telegram.adminbot.control_data import render_dataops
from app.domains.telegram.adminbot.control_keyboards import data_ops_keyboard
from app.domains.telegram.adminbot.helpers import owner_only

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

UNITS_TEXT = "🖥 <b>Fleet Units</b> — failed first, then timers, then managed."
GATES_TEXT = "🚦 <b>Gates</b> — PASS/FAIL per guard, with the last reason."
DATAOPS_TEXT = "🗄 <b>Data Ops</b> — data-plane health; triggers confirm first."
TEXT_LIMIT, MAX_FAILED, MAX_TIMERS = 3400, 14, 6


def _lines(result: dict[str, Any], cap: int = 500) -> list[str]:
    return [ln.strip() for ln in (result.get("output") or "").splitlines() if ln.strip()][:cap]


def _row(kind: str, name: str, label: str) -> list[InlineKeyboardButton]:
    row = [InlineKeyboardButton(f"🔎 {label}", callback_data=f"ctl_unit_{kind}:{name}")]
    if kind == "systemd":  # containers have no enable/disable state
        row.append(InlineKeyboardButton("⏹ disable", callback_data=f"ctl_ask_disable_{kind}:{name}"))
    return row


def units_screen() -> tuple[str, Any]:
    """FAILED services, then armed timers, then the curated managed units."""
    failed_all = _lines(control_ops.failed_units())
    timers_all = _lines(_run_subprocess(["systemctl", "list-timers", "--no-pager", "--no-legend"], 15))
    out = [UNITS_TEXT, f"\n🔴 <b>FAILED ({len(failed_all)})</b>"]
    rows: list[list[InlineKeyboardButton]] = []
    for line in failed_all[:MAX_FAILED]:
        parts = line.split()
        if len(parts) < 2:
            continue
        unit = parts[1].removesuffix(".service")
        desc = " ".join(parts[5:])
        out.append(f"• <code>{escape(unit)}</code> {escape(desc[:44] + '…' if len(desc) > 44 else desc)}")
        rows.append(_row("systemd", unit, unit))
    if len(failed_all) > MAX_FAILED:
        out.append(f"… +{len(failed_all) - MAX_FAILED} more (tap 🔴 Failed Units)")
    out.append(f"\n⏱ <b>TIMERS ({len(timers_all)})</b>")
    for line in timers_all[:MAX_TIMERS]:
        parts = line.split()
        if len(parts) < 2:
            continue
        unit = parts[-2] if parts[-1].endswith(".service") else parts[-1]
        out.append(f"• <code>{escape(unit.removesuffix('.timer'))}</code>")
        rows.append(_row("systemd", unit, unit.removesuffix(".timer")))
    out.append("\n🖥 <b>MANAGED</b>")
    for kind, name, label in control_ops.FLEET_UNITS:
        out.append(f"• <code>{kind}:{escape(name)}</code>")
        rows.append(_row(kind, name, label.split(" ", 1)[-1]))
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return "\n".join(out)[:TEXT_LIMIT], InlineKeyboardMarkup(rows)


def _gate_units() -> list[str]:
    """Registry (control_ops) plus every ``*gate.service`` installed on the host."""
    names = {f"{unit}.service" for unit, _label in control_ops.GATE_UNITS}
    listed = _run_subprocess(["systemctl", "list-unit-files", "--no-pager", "--no-legend", "*gate*.service"], 15)
    names.update(f[0] for f in (ln.split() for ln in _lines(listed)) if f and f[0].endswith("-gate.service"))
    return sorted(names)


def _show(units: list[str], props: str) -> dict[str, dict[str, str]]:
    """One ``systemctl show`` for many units → {Id: {prop: value}}."""
    if not units:
        return {}
    blocks: dict[str, dict[str, str]] = {}
    current: dict[str, str] = {}
    output = _run_subprocess(["systemctl", "show", *units, "-p", f"Id,{props}"], 25).get("output") or ""
    for line in [*output.splitlines(), ""]:
        if not line.strip():
            if current.get("Id"):
                blocks[current["Id"]] = current
            current = {}
            continue
        key, _, value = line.partition("=")
        current[key] = value
    return blocks


def _verdict(state: dict[str, str]) -> tuple[str, str]:
    result, code = state.get("Result", ""), state.get("ExecMainStatus", "")
    if state.get("LoadState", "loaded") != "loaded":
        return "❌", "unit not installed"
    if state.get("ActiveState") == "active":
        return "▶️", "running now"
    if result == "success":
        return "✅", "PASS"
    if result == "exit-code":
        return "❌", f"FAIL exit {code or '?'}"
    return "❌", f"FAIL {result or state.get('ActiveState') or 'unknown'}"


def gates_screen() -> tuple[str, Any]:
    units = _gate_units()
    states = _show(units, "LoadState,ActiveState,Result,ExecMainStatus,Description")
    timers = _show([u.replace(".service", ".timer") for u in units], "LoadState,UnitFileState,ActiveState")
    out, rows, failures = [GATES_TEXT], [], 0
    for unit in units:
        state = states.get(unit, {"LoadState": "not-found", "Description": "unknown unit"})
        emoji, verdict = _verdict(state)
        timer = timers.get(unit.replace(".service", ".timer"), {})
        if timer.get("LoadState") == "not-found":
            note = " · no timer"
        elif timer.get("UnitFileState") in ("disabled", "masked"):
            note = f" · ⚠️ SILENT: timer {timer['UnitFileState']}"
        elif timer.get("ActiveState") != "active":
            note = f" · ⚠️ timer {timer.get('ActiveState', '?')}"
        else:
            note = ""
        detail = state.get("Description", "")[:110]
        if emoji == "❌":
            failures += 1
            if failures <= 3:
                reason = _lines(
                    _run_subprocess(["journalctl", "-u", unit, "-n", "3", "-o", "cat", "--no-pager"], 12), 3
                )
                detail = reason[-1][:110] if reason else detail
        out.append(
            f"{emoji} <b>{escape(unit.removesuffix('.service'))}</b> — {escape(verdict + note)}\n   {escape(detail)}"
        )
        rows.append(
            [
                InlineKeyboardButton("🔎 status", callback_data=f"ctl_unit_systemd:{unit}"),
                InlineKeyboardButton("📜 logs", callback_data=f"ctl_logs_systemd:{unit}"),
            ]
        )
    rows += [
        [InlineKeyboardButton("🔄 Refresh", callback_data="ctl_gates")],
        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
    ]
    return "\n".join(out)[:TEXT_LIMIT], InlineKeyboardMarkup(rows)


def dataops_screen() -> tuple[str, Any]:
    return f"{DATAOPS_TEXT}\n\n{render_dataops()}"[:TEXT_LIMIT], data_ops_keyboard()


@owner_only
async def cmd_units(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the operator's primary systemd view."""
    if update.message is not None:
        text, keyboard = units_screen()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@owner_only
async def cmd_gates(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render PASS/FAIL per gate with its last reason."""
    if update.message is not None:
        text, keyboard = gates_screen()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


@owner_only
async def cmd_dataops(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render data-plane health (Qdrant / ClickHouse / backup freshness)."""
    if update.message is not None:
        text, keyboard = dataops_screen()
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


def register_commands(app: Any) -> None:
    """Attach the three control slash commands in the pre-dispatch group."""
    app.add_handler(CommandHandler("units", cmd_units), group=-1)
    app.add_handler(CommandHandler("gates", cmd_gates), group=-1)
    app.add_handler(CommandHandler("dataops", cmd_dataops), group=-1)
