"""Top-tier menu layout for the admin bot.

Extracted from ``helpers`` (2026-10-16) so the grandfathered helpers module
stays shrink-only. The main menu and control panel are pure layout — no
handler logic — so they live here and are re-exported by ``helpers`` for
backward compatibility.
"""

from __future__ import annotations

from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> Any:
    """Top-tier categorized main menu — 3 sections per row, clear grouping.

    Layout (top → bottom):
      Row 1  📊 System   🐳 Docker   🗄️ Data
      Row 2  🤖 AI       🤖 Bots     🚨 Alerts
      Row 3  ⚙️ Ops      🔧 Tools    ⚡ Quick Ops
      Row 4  🔄 Status   📊 Health   🩺 Self-Test
    Every button resolves to a real handler (verified 2026-10-16).
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 System", callback_data="menu_system"),
                InlineKeyboardButton("🐳 Docker", callback_data="menu_docker"),
                InlineKeyboardButton("🗄️ Data", callback_data="menu_data"),
            ],
            [
                InlineKeyboardButton("🤖 AI", callback_data="menu_ai"),
                InlineKeyboardButton("🤖 Bots", callback_data="menu_bots"),
                InlineKeyboardButton("🚨 Alerts", callback_data="menu_alerts"),
            ],
            [
                InlineKeyboardButton("⚙️ Ops", callback_data="menu_ops"),
                InlineKeyboardButton("🔧 Tools", callback_data="menu_tools"),
                InlineKeyboardButton("⚡ Quick Ops", callback_data="quick"),
            ],
            [
                InlineKeyboardButton("🔄 Quick Status", callback_data="quick_status"),
                InlineKeyboardButton("📊 Health", callback_data="health"),
                InlineKeyboardButton("🩺 Self-Test", callback_data="selftest_run"),
            ],
            [
                InlineKeyboardButton("🖥 Units", callback_data="ctl_units"),
                InlineKeyboardButton("🚦 Gates", callback_data="ctl_gates"),
                InlineKeyboardButton("🗄 Data Ops", callback_data="ctl_dataops"),
            ],
        ]
    )


def control_panel_keyboard() -> Any:
    """/start control panel: quick actions + navigation to sub-menus.

    Top-tier layout — the four most-used actions first, then the section grid.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🖥️ Services", callback_data="services"),
                InlineKeyboardButton("🐳 Docker PS", callback_data="dps"),
            ],
            [
                InlineKeyboardButton("📜 Logs", callback_data="logs_menu"),
                InlineKeyboardButton("📦 Backups", callback_data="admin_backups"),
            ],
            [
                InlineKeyboardButton("🤖 Bot Stats", callback_data="bot_stats"),
                InlineKeyboardButton("🚨 Health", callback_data="health"),
            ],
            [
                InlineKeyboardButton("📊 System", callback_data="menu_system"),
                InlineKeyboardButton("🤖 Bots", callback_data="menu_bots"),
            ],
            [
                InlineKeyboardButton("🔧 Tools", callback_data="menu_tools"),
                InlineKeyboardButton("⚙️ Ops", callback_data="menu_ops"),
            ],
            [InlineKeyboardButton("⚡ Quick Ops", callback_data="quick")],
            [
                InlineKeyboardButton("🖥 Units", callback_data="ctl_units"),
                InlineKeyboardButton("🚦 Gates", callback_data="ctl_gates"),
            ],
            [InlineKeyboardButton("🗄 Data Ops", callback_data="ctl_dataops")],
        ]
    )
