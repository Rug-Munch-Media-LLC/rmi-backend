"""
Admin Bot - Terminal Mode
==========================
Terminal mode handlers: enter, handle messages, exit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram import access_guard
from app.domains.telegram.adminbot import admin_terminal
from app.domains.telegram.adminbot.config import ADMIN_CHAT_ID
from app.domains.telegram.adminbot.helpers import (
    is_owner,
    is_private_chat,
    main_menu_keyboard,
    owner_only,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


def _audit_deny_terminal(user_id: int, action: str, detail: str = "") -> None:
    """Append a deny record to the admin bot audit trail (fail-open)."""
    try:
        from app.domains.telegram.adminbot import admin_audit

        admin_audit.log_action(action, detail, user_id)
    except Exception:
        import logging

        logging.getLogger("admin_bot").debug("terminal audit write failed", exc_info=True)


@owner_only
async def cmd_terminal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Enter terminal mode — every message becomes a shell command."""
    if update.effective_user is None or update.message is None:
        return
    user_id = update.effective_user.id
    admin_terminal.enter_terminal(user_id)
    session = admin_terminal._sessions[user_id]
    prompt = admin_terminal.format_prompt(session)
    await update.message.reply_text(
        f"💻 *TERMINAL MODE*\n\n{prompt}\n\n"
        "Every message is now a shell command.\n"
        "• `cd <dir>` — change directory\n"
        "• `pwd` — show current dir\n"
        "• `exit` — leave terminal mode\n\n"
        f"_Commands are allowlist-gated. Session auto-expires after "
        f"{admin_terminal.TERMINAL_TIMEOUT // 60} min of inactivity. All commands logged._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⚡ Quick Commands", callback_data="term_quick_menu")],
                [InlineKeyboardButton("🚪 Exit Terminal", callback_data="term_exit")],
            ]
        ),
    )


async def handle_terminal_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle messages in terminal mode."""
    if update.effective_user is None or update.message is None:
        return
    user_id = update.effective_user.id
    if not admin_terminal.in_terminal(user_id):
        return
    if not is_private_chat(update):
        return  # DM-only: silently ignore group/channel messages.
    if not is_owner(update):
        # Hidden bot: silent deny even in terminal mode; log + owner alert.
        chat = update.effective_chat
        await access_guard.deny_admin_attempt(
            bot=ctx.bot,
            owner_chat_id=ADMIN_CHAT_ID,
            bot_name="rmiadminbot",
            surface="terminal:message",
            user_id=user_id,
            username=update.effective_user.username,
            chat_id=chat.id if chat else None,
            chat_type=chat.type if chat else None,
            audit=_audit_deny_terminal,
        )
        return

    session = admin_terminal._sessions[user_id]
    text = update.message.text
    if text is None:
        return
    cmd = text.strip()
    result = admin_terminal.execute(session, cmd)

    if result.get("exit"):
        admin_terminal.exit_terminal(user_id)
        await update.message.reply_text(
            "👋 *Exited terminal mode.*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(),
        )
        return

    response_text = admin_terminal.format_result(result, session)
    next_prompt = admin_terminal.format_prompt(session)

    await update.message.reply_text(
        f"{response_text}\n\n{next_prompt}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("⚡ Quick Commands", callback_data="term_quick_menu")],
                [InlineKeyboardButton("🚪 Exit Terminal", callback_data="term_exit")],
            ]
        ),
    )
