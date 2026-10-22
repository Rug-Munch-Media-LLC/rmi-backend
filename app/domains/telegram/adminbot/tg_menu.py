"""Telegram-native ops console for the admin bot.

The shell ``menu`` is right for a keyboard; on Telegram the equivalent is an
inline keyboard: tappable rows, no parsing, no typo risk, and it works from a
phone.  This module is the *map*: sections, labels and the callback-data
contract.  Execution lives in :mod:`tg_menu_actions`, routing in
:mod:`tg_menu_handlers`, so a label can never drift from the action it runs.

Callback-data contract::

    srv|docker|svc|backup|alerts|ai|diag : action          (leaf)
    <section> : do:<target>                                (confirmed mutation)
    noop                                                   (section header)
    menu:refresh                                           (re-render board)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Section title + (label, callback-data) buttons, two per row.
SECTIONS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "🖥 Server",
        (
            ("🩺 Status", "srv:status"),
            ("💾 Disk", "srv:disk"),
            ("🧠 Memory", "srv:mem"),
            ("🔥 Top CPU", "srv:top"),
            ("🖥 Units", "srv:units"),
        ),
    ),
    (
        "🐳 Docker",
        (
            ("📋 Containers", "docker:ps"),
            ("📜 Logs", "docker:logs"),
            ("🔄 Restart", "docker:restart"),
        ),
    ),
    (
        "⚙️ Services",
        (
            ("📋 List", "svc:list"),
            ("📜 Logs", "svc:logs"),
            ("🔄 Restart", "svc:restart"),
        ),
    ),
    (
        "💾 Backups",
        (
            ("📊 Status", "backup:status"),
            ("▶️ Run", "backup:run"),
            ("🔎 Verify", "backup:verify"),
        ),
    ),
    (
        "🔔 Alerts",
        (
            ("📊 Queues", "alerts:status"),
            ("⚙️ Config", "alerts:config"),
        ),
    ),
    (
        "🧠 AI",
        (
            ("🤖 Models", "ai:models"),
            ("❓ Help", "ai:help"),
        ),
    ),
    (
        "🩺 Diagnostics",
        (
            ("🩺 /diag", "diag:run"),
            ("📓 Audit log", "diag:audit"),
            ("📜 Bot log", "diag:log"),
        ),
    ),
)

NOOP = "noop"
REFRESH = "menu:refresh"

#: Group -1 filter — exactly the data this console owns (never legacy data).
CONSOLE_PATTERN = r"^(srv|docker|svc|backup|alerts|ai|diag|menu):|^noop$"


def _leaves() -> Iterator[tuple[str, str]]:
    """Yield (section, action) for every real button in the board."""
    for _title, buttons in SECTIONS:
        for _label, data in buttons:
            section, _, action = data.partition(":")
            yield (section, action)


#: Namespaces owned by this console; other data must fall through to group 0.
NAMESPACES = frozenset(section for section, _ in _leaves())

#: Exact leaf set — the source of truth for "is this a real button?".
LEAVES = frozenset(_leaves())


def _rows(buttons: tuple[tuple[str, str], ...]) -> list[list[InlineKeyboardButton]]:
    """Lay buttons out two per row, the Telegram convention."""
    return [
        [InlineKeyboardButton(label, callback_data=data) for label, data in buttons[i : i + 2]]
        for i in range(0, len(buttons), 2)
    ]


def build_menu() -> InlineKeyboardMarkup:
    """The full ops board: seven sections plus a refresh row."""
    rows: list[list[InlineKeyboardButton]] = []
    for title, buttons in SECTIONS:
        rows.append([InlineKeyboardButton(title, callback_data=NOOP)])
        rows.extend(_rows(buttons))
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data=REFRESH)])
    return InlineKeyboardMarkup(rows)


def find_handler(callback_data: str) -> tuple[str, str] | None:
    """Resolve callback-data to ``(section, action)``, or None when not ours.

    Resolution is data-driven from :data:`SECTIONS`, so adding a button can
    never leave it unresolved (the "dead button" failure mode).  ``do:``
    targets are the second step of a confirmed mutation and are accepted for
    any console namespace — the executor validates the target name.
    """
    if callback_data == NOOP:
        return ("menu", "noop")
    if callback_data == REFRESH:
        return ("menu", "refresh")
    section, _, action = callback_data.partition(":")
    if not action or section not in NAMESPACES:
        return None
    if (section, action) in LEAVES or action.startswith("do:"):
        return (section, action)
    return None
