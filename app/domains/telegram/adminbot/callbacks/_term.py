"""
Admin Bot - Callback handlers: terminal
=========================================
Terminal quick-command menus, mode entry/exit, and the per-invocation
confirmation buttons for mutating commands. Destructive commands never get a
button — they require the code to be typed back in chat
(``admin_terminal.confirm(..., typed=True)``).

Dispatched by ``callbacks._dispatch.handle_callback`` in original chain order.
Returns True when a branch matched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_terminal, redact
from app.domains.telegram.adminbot.helpers import back_button, main_menu_keyboard

if TYPE_CHECKING:
    from telegram import CallbackQuery


def _result_text(result: dict) -> str:
    body = redact.scrub(f"```\n{(result.get('output') or result.get('error') or '')[:2000]}\n```")
    return f"{'✅' if result['ok'] else '❌'} {body}"


async def _confirm(data: str, query: CallbackQuery) -> None:
    """Approve or cancel a staged command from its inline keyboard."""
    nonce = data.split(":", 1)[1]
    user_id = query.from_user.id
    session = admin_terminal._sessions.get(user_id)
    if session is None or not admin_terminal.in_terminal(user_id):
        await query.edit_message_text("⌛️ Terminal session expired — use /term again.")
        return
    if data.startswith("term_no:"):
        admin_terminal.cancel(session)
        await query.edit_message_text("✖ Cancelled — nothing ran.", reply_markup=main_menu_keyboard())
        return
    result = admin_terminal.confirm(session, nonce, user_id, typed=False)
    await query.edit_message_text(
        _result_text(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚪 Exit Terminal", callback_data="term_exit")]]),
    )


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle terminal callback data. Returns True when matched."""
    # ── Terminal callbacks ──
    if data == "term_enter":
        await query.edit_message_text(
            "💻 Use `/term` to enter terminal mode from the chat.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("main"),
        )
        return True
    elif data == "term_quick_menu":
        await query.edit_message_text(
            "⚡ *Terminal Quick Commands* — pick one to run:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_terminal.quick_commands_keyboard(),
        )
        return True
    elif data.startswith("term_quick_"):
        cmd_name = data.replace("term_quick_", "")
        admin_terminal.touch(query.from_user.id)
        result = admin_terminal.run_quick(cmd_name, query.from_user.id)
        await query.edit_message_text(
            f"{'✅' if result['ok'] else '❌'} Quick: `{cmd_name}`\n\n```\n{result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("⚡ More Commands", callback_data="term_quick_menu")],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
        )
        return True
    elif data.startswith(("term_ok:", "term_no:")):
        await _confirm(data, query)
        return True
    elif data == "term_exit":
        admin_terminal.exit_terminal(query.from_user.id)
        await query.edit_message_text(
            "👋 Exited terminal mode.",
            reply_markup=main_menu_keyboard(),
        )
        return True

    return False
