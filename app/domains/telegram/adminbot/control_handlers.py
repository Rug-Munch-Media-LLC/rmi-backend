"""Handlers for the server-control surface — one branch per button.

Registered in the pre-dispatch group (-1) so the legacy generic callback
router never sees these prefixes. Every button in ``control_keyboards`` has a
branch here; the confirm step is the only path to ``control_actions``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler

from app.domains.telegram.adminbot import control_actions, control_ops
from app.domains.telegram.adminbot.control_keyboards import (
    confirm_keyboard,
    data_ops_keyboard,
    gates_keyboard,
    unit_card_keyboard,
    units_keyboard,
)
from app.domains.telegram.adminbot.helpers import back_button, owner_only

if TYPE_CHECKING:
    from telegram import CallbackQuery, Update
    from telegram.ext import ContextTypes

logger = logging.getLogger("admin_bot.control")

UNITS_TEXT = "🖥 <b>Fleet Units</b> — tap a unit to control it."
GATES_TEXT = "🚦 <b>Gates</b> — last result per guard."


def _fmt(result: dict[str, Any], limit: int = 3000) -> str:
    body = result.get("output") or result.get("error") or "(no output)"
    return str(body)[:limit]


@owner_only
async def cmd_units(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the fleet-unit picker."""
    if update.message is None:
        return
    await update.message.reply_text(UNITS_TEXT, parse_mode=ParseMode.HTML, reply_markup=units_keyboard())


@owner_only
async def cmd_gates(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the gates screen."""
    if update.message is None:
        return
    result = control_ops.gate_status()
    await update.message.reply_text(
        f"{GATES_TEXT}\n<pre>{_fmt(result)}</pre>",
        parse_mode=ParseMode.HTML,
        reply_markup=gates_keyboard(),
    )


@owner_only
async def cmd_dataops(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the data-ops picker."""
    if update.message is None:
        return
    await update.message.reply_text(
        "🗄 <b>Data Ops</b> — each trigger asks for confirmation.",
        parse_mode=ParseMode.HTML,
        reply_markup=data_ops_keyboard(),
    )


async def _show_unit_card(query: CallbackQuery, kind: str, name: str) -> None:
    result = control_ops.unit_status(kind, name)
    await query.edit_message_text(
        f"🖥 <b>{kind}:{name}</b>\n<pre>{_fmt(result, 500)}</pre>",
        parse_mode=ParseMode.HTML,
        reply_markup=unit_card_keyboard(kind, name),
    )


async def _ask_confirm(query: CallbackQuery, action: str, kind: str, name: str) -> None:
    token = control_actions.mint_token(action, kind, name)
    await query.edit_message_text(
        f"⚠️ <b>Confirm {action}</b>\n\n{action} <code>{kind}:{name}</code>?\n"
        f"<i>Expires in {control_actions.CONFIRM_TTL_SECONDS}s.</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=confirm_keyboard(action, kind, name, token),
    )


async def _do_action(query: CallbackQuery, action: str, token: str) -> None:
    resolved = control_actions.consume_token(token)
    if resolved is None:
        await query.edit_message_text("⌛ Confirmation expired or already used.", reply_markup=back_button("ctl_units"))
        return
    _act, kind, name = resolved
    result = control_actions.run_unit_action(action, kind, name, query.from_user.id)
    emoji = "✅" if result.get("ok") else "❌"
    await query.edit_message_text(
        f"{emoji} <b>{action} {kind}:{name}</b>\n<pre>{_fmt(result, 2000)}</pre>",
        parse_mode=ParseMode.HTML,
        reply_markup=unit_card_keyboard(kind, name),
    )


async def _do_trigger(query: CallbackQuery, token: str) -> None:
    resolved = control_actions.consume_token(token)
    if resolved is None:
        await query.edit_message_text(
            "⌛ Confirmation expired or already used.", reply_markup=back_button("ctl_dataops")
        )
        return
    _act, _kind, op_key = resolved
    result = control_actions.run_data_op(op_key, query.from_user.id)
    emoji = "✅" if result.get("ok") else "❌"
    await query.edit_message_text(
        f"{emoji} <b>trigger {op_key}</b> queued\n<pre>{_fmt(result, 1500)}</pre>",
        parse_mode=ParseMode.HTML,
        reply_markup=data_ops_keyboard(),
    )


async def _tap_units(query: CallbackQuery) -> None:
    await query.edit_message_text(UNITS_TEXT, parse_mode=ParseMode.HTML, reply_markup=units_keyboard())


async def _tap_gates(query: CallbackQuery) -> None:
    result = control_ops.gate_status()
    await query.edit_message_text(
        f"{GATES_TEXT}\n<pre>{_fmt(result)}</pre>", parse_mode=ParseMode.HTML, reply_markup=gates_keyboard()
    )


async def _tap_dataops(query: CallbackQuery) -> None:
    await query.edit_message_text(
        "🗄 <b>Data Ops</b> — each trigger asks for confirmation.",
        parse_mode=ParseMode.HTML,
        reply_markup=data_ops_keyboard(),
    )


async def _tap_vitals(query: CallbackQuery) -> None:
    result = control_ops.host_vitals()
    await query.edit_message_text(
        f"📈 <b>Host Vitals</b>\n<pre>{_fmt(result)}</pre>",
        parse_mode=ParseMode.HTML,
        reply_markup=back_button("main"),
    )


async def _tap_failed(query: CallbackQuery) -> None:
    result = control_ops.failed_units()
    body = _fmt(result) or "🟢 no failed units"
    await query.edit_message_text(
        f"🔴 <b>Failed Units</b>\n<pre>{body}</pre>",
        parse_mode=ParseMode.HTML,
        reply_markup=back_button("main"),
    )


async def _tap_unit_card(query: CallbackQuery, data: str, prefix: str) -> None:
    parsed = control_ops.parse_unit_key(data[len(prefix) :])
    if parsed:
        await _show_unit_card(query, *parsed)


async def _tap_logs(query: CallbackQuery, data: str) -> None:
    parsed = control_ops.parse_unit_key(data[len("ctl_logs_") :])
    if not parsed:
        return
    kind, name = parsed
    result = control_ops.unit_logs(kind, name, 40)
    await query.edit_message_text(
        f"📜 <b>{kind}:{name}</b> (last 40)\n<pre>{_fmt(result)}</pre>",
        parse_mode=ParseMode.HTML,
        reply_markup=unit_card_keyboard(kind, name),
    )


async def _tap_ask(query: CallbackQuery, data: str) -> None:
    action, _, key = data[len("ctl_ask_") :].partition("_")
    if action == "trigger":
        token = control_actions.mint_token("trigger", "data", key)
        await query.edit_message_text(
            f"⚠️ <b>Confirm trigger</b>\n\nQueue data op <code>{key}</code>?\n"
            f"<i>Expires in {control_actions.CONFIRM_TTL_SECONDS}s.</i>",
            parse_mode=ParseMode.HTML,
            reply_markup=confirm_keyboard("trigger", "data", key, token),
        )
        return
    parsed = control_ops.parse_unit_key(key)
    if parsed:
        await _ask_confirm(query, action, *parsed)


async def _tap_do(query: CallbackQuery, data: str) -> None:
    action, _, token = data[len("ctl_do_") :].partition("_")
    if action == "trigger":
        await _do_trigger(query, token)
    else:
        await _do_action(query, action, token)


async def _tap_cancel(query: CallbackQuery) -> None:
    await query.edit_message_text("❌ Cancelled.", reply_markup=back_button("ctl_units"))


# Exact-match buttons → handler. Prefix buttons are handled in _tap_prefixed.
_EXACT_TAPS = {
    "ctl_units": _tap_units,
    "ctl_gates": _tap_gates,
    "ctl_dataops": _tap_dataops,
    "ctl_vitals": _tap_vitals,
    "ctl_failed": _tap_failed,
    "ctl_no_": _tap_cancel,
}


async def _tap_prefixed(query: CallbackQuery, data: str) -> bool:
    """Dispatch a ``ctl_*`` prefix button. Returns True when handled."""
    if data.startswith("ctl_unit_"):
        await _tap_unit_card(query, data, "ctl_unit_")
    elif data.startswith("ctl_status_"):
        await _tap_unit_card(query, data, "ctl_status_")
    elif data.startswith("ctl_logs_"):
        await _tap_logs(query, data)
    elif data.startswith("ctl_ask_"):
        await _tap_ask(query, data)
    elif data.startswith("ctl_do_"):
        await _tap_do(query, data)
    else:
        return False
    return True


async def on_control_tap(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Route every ``ctl_*`` callback. One branch per button."""
    query: Any = update.callback_query
    await query.answer()
    data: str = query.data or ""

    exact = _EXACT_TAPS.get(data)
    if exact is not None:
        await exact(query)
        return
    if await _tap_prefixed(query, data):
        return

    logger.warning("ctl UNHANDLED data=%s", data)
    await query.edit_message_text("🚧 Unknown control action.", reply_markup=back_button("main"))


def register(app: Any) -> None:
    """Attach control handlers in the pre-dispatch group (-1)."""
    app.add_handler(CommandHandler("units", cmd_units), group=-1)
    app.add_handler(CommandHandler("gates", cmd_gates), group=-1)
    app.add_handler(CommandHandler("dataops", cmd_dataops), group=-1)
    app.add_handler(CallbackQueryHandler(on_control_tap, pattern=r"^ctl_"), group=-1)
    logger.info("server-control handlers registered in pre-dispatch group")
