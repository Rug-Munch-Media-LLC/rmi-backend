"""Keyboards for the server-control surface (units, gates, data ops, confirm).

Pure layout — no handler logic. Every button here has a handler in
``control_handlers``; the confirm keyboard is the only path to a mutation.
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.domains.telegram.adminbot.control_ops import DATA_OPS, FLEET_UNITS


def _grid(buttons: list[tuple[str, str]], cols: int = 2) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for label, data in buttons:
        row.append(InlineKeyboardButton(label, callback_data=data))
        if len(row) == cols:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def units_keyboard() -> Any:
    """Picker: every fleet unit, tap to open its control card."""
    buttons = [(label, f"ctl_unit_{kind}:{name}") for kind, name, label in FLEET_UNITS]
    rows = _grid(buttons)
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def unit_card_keyboard(kind: str, name: str) -> Any:
    """Per-unit card: status/logs (safe) + start/stop/restart (confirm-gated)."""
    key = f"{kind}:{name}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 Status", callback_data=f"ctl_status_{key}"),
                InlineKeyboardButton("📜 Logs", callback_data=f"ctl_logs_{key}"),
            ],
            [
                InlineKeyboardButton("▶️ Start", callback_data=f"ctl_ask_start_{key}"),
                InlineKeyboardButton("🔄 Restart", callback_data=f"ctl_ask_restart_{key}"),
                InlineKeyboardButton("⏹ Stop", callback_data=f"ctl_ask_stop_{key}"),
            ],
            [InlineKeyboardButton("⬅️ Units", callback_data="ctl_units")],
        ]
    )


def confirm_keyboard(action: str, kind: str, name: str, token: str) -> Any:
    """The mandatory confirm step: ✅ executes, ❌ cancels. Token is single-use."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=f"ctl_do_{action}_{token}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"ctl_no_{token}"),
            ]
        ]
    )


def data_ops_keyboard() -> Any:
    """Data ops: each trigger is confirm-gated (they mutate the corpus)."""
    buttons = [(label, f"ctl_ask_trigger_{key}") for key, label, _unit in DATA_OPS]
    rows = _grid(buttons)
    rows.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return InlineKeyboardMarkup(rows)


def gates_keyboard() -> Any:
    """Gates screen: refresh + back."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Refresh", callback_data="ctl_gates")],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
        ]
    )
