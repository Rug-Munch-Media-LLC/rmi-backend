"""Build the model picker keyboard."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def build(models: list[dict[str, object]]) -> InlineKeyboardMarkup:
    """One button per lane; a dot marks the lanes that are answering.

    Callback data is the lane id, so a stale button can only ever select a lane
    that still exists in LANES.
    """
    rows = [
        [
            InlineKeyboardButton(
                f"{'🟢' if m['up'] else '⚪️'} {m['label']}",
                callback_data=f"model:{m['id']}",
            )
        ]
        for m in models
    ]
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data="model:refresh")])
    return InlineKeyboardMarkup(rows)
