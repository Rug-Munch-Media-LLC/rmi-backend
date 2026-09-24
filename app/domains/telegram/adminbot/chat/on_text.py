"""Handle plain-language messages as commands."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .audit import record
from .intents import OPEN_INTENTS, classify
from .owner import is_owner

if TYPE_CHECKING:
    from telegram import Update

logger = logging.getLogger("adminbot.chat.text")


async def on_text(update: Update, context: object) -> None:
    """Translate a plain request into an existing command, or ask for clarity.

    Nothing is executed here: the translated text is handed back for the bot's
    own dispatcher, so the existing guards and handlers remain the only path to
    an action. A request the owner may not run is refused and audited.
    """
    text = update.message.text or ""
    command = classify(text)
    if command is None:
        await update.message.reply_text("🤖 Try /help, or ask: 'restart nginx'.")
        return
    if command not in OPEN_INTENTS and not is_owner(update.effective_user.id):
        record("chat.denied", command, update.effective_user.id)
        await update.message.reply_text("🔒 Owner only.")
        return
    record("chat.route", command, update.effective_user.id)
    await update.message.reply_text(f"▶️ <code>{command}</code>")
