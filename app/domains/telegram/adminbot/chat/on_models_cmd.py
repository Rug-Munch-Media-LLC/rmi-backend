"""Handle /models."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .models_discover import discover
from .models_keyboard import build
from .owner import is_owner
from .parts import read

if TYPE_CHECKING:
    from telegram import Update

logger = logging.getLogger("adminbot.chat.models_cmd")


async def on_models(update: Update, context: object) -> None:
    """Render the picker for the owner, or a refusal for anyone else."""
    parts = read(update)
    if parts is None:
        return
    if not is_owner(parts.user_id):
        await parts.message.reply_text("🔒 Owner only.")
        return
    await parts.message.reply_text("🤖 Select a model lane", reply_markup=build(discover()))
