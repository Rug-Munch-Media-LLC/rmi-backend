"""Callback router for the control surface — one branch per button, group -1.

Commands are defined once in ``control_commands``; this module owns only the
``ctl_*`` taps. The confirm step is the only path to ``control_actions``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler

from app.domains.telegram.adminbot import control_actions, control_commands, control_ops
from app.domains.telegram.adminbot.control_keyboards import (
    confirm_keyboard,
    data_ops_keyboard,
    unit_card_keyboard,
)
from app.domains.telegram.adminbot.helpers import back_button, owner_only

if TYPE_CHECKING:
    from telegram import CallbackQuery, Update
    from telegram.ext import ContextTypes

logger = logging.getLogger("admin_bot.control")


def _fmt(result: dict[str, Any], limit: int = 3000) -> str:
    body = result.get("output") or result.get("error") or "(no output)"
    return str(body)[:limit]


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


async def _do(query: CallbackQuery, action: str, token: str) -> None:
    """Execute a confirmed action — the only path to ``control_actions``."""
    resolved = control_actions.consume_token(token)
    if resolved is None:
        back = "ctl_dataops" if action == "trigger" else "ctl_units"
        await query.edit_message_text("⌛ Confirmation expired or already used.", reply_markup=back_button(back))
        return
    _act, kind, name = resolved
    if action == "trigger":
        result = control_actions.run_data_op(name, query.from_user.id)
        markup, title = data_ops_keyboard(), f"trigger {name} queued"
    else:
        result = control_actions.run_unit_action(action, kind, name, query.from_user.id)
        markup, title = unit_card_keyboard(kind, name), f"{action} {kind}:{name}"
    emoji = "✅" if result.get("ok") else "❌"
    await query.edit_message_text(
        f"{emoji} <b>{title}</b>\n<pre>{_fmt(result, 2000)}</pre>", parse_mode=ParseMode.HTML, reply_markup=markup
    )


async def _screen(query: CallbackQuery, builder: Any) -> None:
    text, keyboard = builder()
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def _tap_units(query: CallbackQuery) -> None:
    await _screen(query, control_commands.units_screen)


async def _tap_gates(query: CallbackQuery) -> None:
    await _screen(query, control_commands.gates_screen)


async def _tap_dataops(query: CallbackQuery) -> None:
    await _screen(query, control_commands.dataops_screen)


async def _tap_vitals(query: CallbackQuery) -> None:
    text = f"📈 <b>Host Vitals</b>\n<pre>{_fmt(control_ops.host_vitals())}</pre>"
    await _screen(query, lambda: (text, back_button("main")))


async def _tap_failed(query: CallbackQuery) -> None:
    body = _fmt(control_ops.failed_units(), 3500) or "🟢 no failed units"
    text = f"🔴 <b>Failed Units</b>\n<pre>{body}</pre>"
    await _screen(query, lambda: (text, back_button("main")))


async def _tap_unit_card(query: CallbackQuery, data: str, prefix: str) -> None:
    parsed = control_ops.parse_unit_key(data[len(prefix) :])
    if parsed:
        await _show_unit_card(query, *parsed)


async def _tap_logs(query: CallbackQuery, data: str) -> None:
    kind, name = control_ops.parse_unit_key(data[len("ctl_logs_") :]) or ("", "")
    if not kind:
        return
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
    await _do(query, action, token)


async def _tap_cancel(query: CallbackQuery) -> None:
    await query.edit_message_text("❌ Cancelled.", reply_markup=back_button("ctl_units"))


# Exact-match buttons → handler. Prefix buttons are handled in _tap_prefixed.
_EXACT_TAPS = {
    "ctl_units": _tap_units,
    "ctl_gates": _tap_gates,
    "ctl_dataops": _tap_dataops,
    "ctl_vitals": _tap_vitals,
    "ctl_failed": _tap_failed,
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
    elif data.startswith("ctl_no_"):
        await _tap_cancel(query)
    else:
        return False
    return True


@owner_only
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
    """Attach the control surface in the pre-dispatch group (-1).

    Commands come from ``control_commands`` (single canonical definition);
    this module owns only the ``ctl_*`` callback router.
    """
    control_commands.register_commands(app)
    app.add_handler(CallbackQueryHandler(on_control_tap, pattern=r"^ctl_"), group=-1)
    logger.info("server-control handlers registered in pre-dispatch group")
