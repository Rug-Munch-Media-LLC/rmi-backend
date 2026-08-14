"""
Admin Bot - Callback handlers: terminal
=========================================
Terminal quick-command menus and mode entry/exit.
Dispatched by ``callbacks._dispatch.handle_callback`` in original chain
order. Returns True when a branch matched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_terminal
from app.domains.telegram.adminbot.helpers import back_button, main_menu_keyboard

if TYPE_CHECKING:
    from telegram import CallbackQuery


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
        result = admin_terminal.run_quick(cmd_name)
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
    elif data == "term_exit":
        admin_terminal.exit_terminal(query.from_user.id)
        await query.edit_message_text(
            "👋 Exited terminal mode.",
            reply_markup=main_menu_keyboard(),
        )
        return True

    return False
