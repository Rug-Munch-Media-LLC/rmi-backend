"""Refresh button for the self-test message."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def selftest_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔄 Re-run", callback_data="selftest_run"),
                InlineKeyboardButton("⬅️ Main Menu", callback_data="main"),
            ]
        ]
    )
