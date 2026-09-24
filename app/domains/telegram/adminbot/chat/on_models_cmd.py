"""Handle /models."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Update

from .models_discover import discover
from .models_keyboard import build
from .owner import is_owner


async def on_models(update: Update, context: object) -> None:
    """Render the picker for the owner, or a refusal for anyone else."""
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("🔒 Owner only.")
        return
    await update.message.reply_text("🤖 Select a model lane", reply_markup=build(discover()))
