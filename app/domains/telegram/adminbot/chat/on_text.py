"""Handle plain-language messages as commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .audit import record
from .intents import OPEN_INTENTS, classify
from .owner import is_owner
from .parts import read

if TYPE_CHECKING:
    from telegram import Update

logger = logging.getLogger("adminbot.chat.text")


async def on_text(update: Update, context: Any) -> None:
    """Translate a plain request into an existing command and run it.

    The translated command is dispatched through the bot's own handler map, so
    the existing owner gate and rate limiter remain the only path to an action.
    A request the owner may not run is refused and audited.
    """
    parts = read(update)
    if parts is None:
        return
    command = classify(parts.message.text or "")
    if command is None:
        await parts.message.reply_text("🤖 Try /help, or ask: 'restart nginx'.")
        return
    if command not in OPEN_INTENTS and not is_owner(parts.user_id):
        record("chat.denied", command, parts.user_id)
        await parts.message.reply_text("🔒 Owner only.")
        return
    record("chat.route", command, parts.user_id)
    await _dispatch(update, context, command)


async def _dispatch(update: Update, context: Any, command: str) -> None:
    """Run the translated command via the registered CommandHandler.

    The real PTB context is forwarded, so the handler sees the same
    ``context.args``/``user_data`` it would from a slash command. Falls back to
    a hint when the command has no executable handler (e.g. the ``__models__``
    sentinel), so the owner is never left with a silent no-op.
    """
    from app.domains.telegram.adminbot.bot_commands import COMMAND_HANDLERS

    handler = COMMAND_HANDLERS.get(command)
    if handler is None:
        await update.effective_message.reply_text(f"▶️ Use `/{command}` — no direct runner for this phrase.")
        return
    await handler(update, context)
