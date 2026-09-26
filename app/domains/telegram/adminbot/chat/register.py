"""Register the chat handlers on the existing application."""

from __future__ import annotations

from typing import Any

from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from .on_model import on_pick
from .on_models_cmd import on_models
from .on_text_handler import handler as text_handler


def register(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    """Attach model picker and natural-language handlers.

    Called from the bot's existing registration path. The text handler is added
    last so explicit commands keep priority over plain-language routing.
    """
    # CommandHandler's first type argument is the CallbackContext payload type,
    # NOT the callback's return type; None is that return type. The default
    # context carries dict user_data, so DEFAULT_TYPE is the concrete match.
    app.add_handler(CommandHandler[ContextTypes.DEFAULT_TYPE, None]("models", on_models))
    app.add_handler(CallbackQueryHandler(on_pick, pattern=r"^model:"))
    app.add_handler(text_handler())
