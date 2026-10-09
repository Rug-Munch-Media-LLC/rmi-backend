"""
AI keyboard builders — extracted from admin_ai.py for structure gate.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.adminbot.admin_ai import MODELS


def model_keyboard() -> Any:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    buttons: list[list[Any]] = []
    row: list[Any] = []
    for mid in MODELS:
        row.append(InlineKeyboardButton(MODELS[mid]["name"], callback_data=f"ai_model_{mid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("🧹 Clear History", callback_data="ai_clear")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="main")])
    return InlineKeyboardMarkup(buttons)


def list_models() -> str:
    lines = ["🤖 *Available AI Models*\n"]
    for mid, info in MODELS.items():
        lines.append(f"• `{mid}` — {info['name']} ({info['provider']})")
    lines.append("\nUse `/ai ask <prompt>` for a read-only query, or `/oc <prompt>` for a confirmed tooled run.")
    return "\n".join(lines)
