"""
Admin Bot - Terminal Mode
==========================
Terminal mode handlers: enter, handle messages, exit.

Presentation layer only — classification, confirmation state and execution
live in ``admin_terminal`` / ``terminal_policy``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram import access_guard
from app.domains.telegram.adminbot import admin_terminal, redact, terminal_policy as policy
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


def prompt(session: dict[str, Any]) -> str:
    return f"`{session.get('cwd', policy.DEFAULT_CWD)}$`"


def render_result(result: dict[str, Any], session: dict[str, Any]) -> str:
    """Render one execution result (already redacted, truncated to MAX_OUTPUT)."""
    output = result.get("output") or result.get("error") or ""
    body = f"```\n{output[: admin_terminal.MAX_OUTPUT]}\n```"
    tier = result.get("tier")
    return f"⚠️ _confirmed {tier}_\n{body}" if tier in (policy.MUTATE, policy.DANGER) else body


def pending_text(pending: dict[str, Any]) -> str:
    """Describe a staged command and how to confirm it."""
    command = " ".join(pending["argv"])[:400]
    ttl = admin_terminal.CONFIRM_TTL
    if pending["tier"] == policy.DANGER:
        return (
            f"☠️ *DESTRUCTIVE COMMAND*\n\n`{command}`\n\n"
            f"Type `confirm {pending['nonce']}` within {ttl}s to run it — this can take production down."
        )
    return f"⚠️ *MUTATING COMMAND*\n\n`{command}`\n\nConfirm below to run it once (expires in {ttl}s)."


def pending_keyboard(pending: dict[str, Any]) -> InlineKeyboardMarkup:
    """Destructive commands have no button — only the typed code can approve them."""
    nonce = pending["nonce"]
    cancel = InlineKeyboardButton("✖ Cancel", callback_data=f"term_no:{nonce}")
    if pending["tier"] == policy.DANGER:
        return InlineKeyboardMarkup([[cancel]])
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Run once", callback_data=f"term_ok:{nonce}"), cancel]])


def _terminal_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⚡ Quick Commands", callback_data="term_quick_menu")],
            [InlineKeyboardButton("🚪 Exit Terminal", callback_data="term_exit")],
        ]
    )


@owner_only
async def cmd_terminal(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Enter terminal mode — every message becomes a shell command."""
    if update.effective_user is None or update.message is None:
        return
    admin_terminal.enter_terminal(update.effective_user.id)
    session = admin_terminal._sessions[update.effective_user.id]
    await update.message.reply_text(
        f"💻 *TERMINAL MODE*\n\n{prompt(session)}\n\n"
        "Every message is now a shell command.\n"
        "• read-only commands run immediately\n"
        "• mutating commands need a confirmation button\n"
        "• destructive commands need the typed code\n"
        "• `exit` — leave terminal mode\n\n"
        f"_Jailed to {', '.join(policy.ROOTS)}. Session expires after "
        f"{admin_terminal.TERMINAL_TIMEOUT // 60} min idle / {admin_terminal.TERMINAL_MAX_LIFETIME // 60} min total. "
        "Every command is audited._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_terminal_keyboard(),
    )


async def _send_result(update: Update, result: dict[str, Any], session: dict[str, Any]) -> None:
    body = redact.scrub(render_result(result, session))
    await update.message.reply_text(
        f"{body}\n\n{prompt(session)}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=_terminal_keyboard(),
    )


async def _send_pending(update: Update, pending: dict[str, Any], session: dict[str, Any]) -> None:
    await update.message.reply_text(
        f"{pending_text(pending)}\n\n{prompt(session)}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=pending_keyboard(pending),
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

    if cmd.lower().startswith("confirm "):
        nonce = cmd.split(maxsplit=1)[1].strip()
        await _send_result(update, admin_terminal.confirm(session, nonce, user_id, typed=True), session)
        return

    result = admin_terminal.execute(session, cmd, user_id)
    if result.get("exit"):
        admin_terminal.exit_terminal(user_id)
        await update.message.reply_text(
            "👋 *Exited terminal mode.*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(),
        )
        return
    if result.get("pending"):
        await _send_pending(update, result["pending"], session)
        return
    await _send_result(update, result, session)
