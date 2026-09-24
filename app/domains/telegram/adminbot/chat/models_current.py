"""Read the owner's selected model lane."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

KEY = "selected_model"
DEFAULT = "local-workhorse"


def current(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Return the selected lane for this chat, defaulting to the workhorse."""
    return (context.user_data or {}).get(KEY, DEFAULT)
