"""Handle model-picker button presses."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .audit import record
from .models_choose import choose
from .models_discover import discover
from .models_keyboard import build
from .owner import is_owner

if TYPE_CHECKING:
    from telegram import CallbackQuery, Update
    from telegram.ext import ContextTypes

logger = logging.getLogger("adminbot.chat.models")


async def on_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Acknowledge immediately, then render the picker or confirm a choice."""
    query: CallbackQuery = update.callback_query
    await query.answer()
    if not is_owner(query.from_user.id):
        await query.edit_message_text("🔒 Owner only.")
        return
    _, _, lane_id = (query.data or "::").partition(":")
    if lane_id == "refresh":
        await query.edit_message_text("🤖 Models", reply_markup=build(discover()))
        return
    label = choose(context, lane_id)
    record("model.select", label, query.from_user.id)
    await query.edit_message_text(f"✅ Model set: <b>{label}</b>")
