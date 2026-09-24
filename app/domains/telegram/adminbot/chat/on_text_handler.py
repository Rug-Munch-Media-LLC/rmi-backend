"""The plain-language text handler."""

from __future__ import annotations

from typing import Any

from telegram.ext import MessageHandler, filters

from .on_text import on_text


def handler() -> MessageHandler[Any, Any]:
    """Return the text handler, registered last so explicit commands win."""
    return MessageHandler(filters.TEXT & ~filters.COMMAND, on_text)
