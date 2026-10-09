"""
Message & callback handler registration — extracted from bot.py for structure gate.
"""

from __future__ import annotations

from typing import Any

from telegram import BotCommand
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from app.domains.telegram.adminbot.callbacks import handle_callback
from app.domains.telegram.adminbot.config import CANONICAL_COMMANDS
from app.domains.telegram.adminbot.terminal import handle_terminal_message


def register_message_handlers(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Register message and callback handlers."""
    # Terminal mode — catch ALL text messages when in terminal
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_terminal_message),
        group=0,
    )

    # Callback queries (inline keyboards)
    app.add_handler(CallbackQueryHandler(handle_callback))


async def publish_command_menu(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Replace Telegram's cached slash-command list with the curated set."""
    try:
        await app.bot.set_my_commands(
            [BotCommand(command=command, description=description) for command, description in CANONICAL_COMMANDS]
        )
    except Exception as exc:
        import logging

        logger = logging.getLogger("admin_bot.handlers")
        logger.warning("Telegram command menu publish failed: %s", exc)
