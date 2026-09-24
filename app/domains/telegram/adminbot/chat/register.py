"""Register the chat handlers on the existing application."""

from __future__ import annotations

from typing import Any

from telegram.ext import CallbackQueryHandler, CommandHandler

from .on_model import on_pick
from .on_models_cmd import on_models
from .on_text_handler import handler as text_handler


def register(app: Any) -> None:
    """Attach model picker and natural-language handlers.

    Called from the bot's existing registration path. The text handler is added
    last so explicit commands keep priority over plain-language routing.
    """
    app.add_handler(CommandHandler("models", on_models))
    app.add_handler(CallbackQueryHandler(on_pick, pattern=r"^model:"))
    app.add_handler(text_handler())
