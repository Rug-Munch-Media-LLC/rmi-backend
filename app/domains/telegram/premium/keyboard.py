"""Inline keyboard for a scan card: Full scan | Watch | Chart.

Rows are returned as plain dicts so the renderer stays importable without
``python-telegram-bot`` installed (tests, dry-run tooling, the API). ``as_markup``
does the one lazy import at the edge — the only place that touches PTB.

``callback_data`` is namespaced and stays under Telegram's 64-byte limit:
``scan:<action>:<address>`` = 11 + 44 bytes at most on Solana.
"""

from __future__ import annotations

ACTIONS = (("Full scan", "full"), ("Watch", "watch"), ("Chart", "chart"))


def scan_keyboard(address: str, chain: str = "solana") -> tuple[tuple[dict[str, str], ...], ...]:
    """One row, three actions, each bound to this token and chain."""
    token = f"{chain.lower()}:{address}"
    return ((tuple({"text": label, "callback_data": f"scan:{action}:{token}"} for label, action in ACTIONS)),)


def as_markup(rows: tuple[tuple[dict[str, str], ...], ...]):
    """Convert to ``InlineKeyboardMarkup``; the single Telegram dependency edge."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(button["text"], callback_data=button["callback_data"]) for button in row]
            for row in rows
        ]
    )
