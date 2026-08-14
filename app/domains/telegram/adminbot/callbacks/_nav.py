"""
Admin Bot - Callback handlers: navigation & status cards
==========================================================
Main menu, section sub-menus, quick/health status cards, and quick ops.
Dispatched by ``callbacks._dispatch.handle_callback`` in original chain
order. Returns True when a branch matched (dispatcher stops).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_health, admin_ops, admin_server
from app.domains.telegram.adminbot.helpers import (
    back_button,
    control_panel_keyboard,
    refresh_keyboard,
    section_menu_keyboard,
    section_title,
)

if TYPE_CHECKING:
    from telegram import CallbackQuery


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle nav/status callback data. Returns True when matched."""
    # ── Main navigation ──
    if data == "main":
        qs = admin_health.control_panel()
        card = admin_health.format_control_panel(qs)
        await query.edit_message_text(
            f"{card}",
            reply_markup=control_panel_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return True

    # ── Section sub-menus ──
    elif data.startswith("menu_"):
        section = data.replace("menu_", "")
        await query.edit_message_text(
            f"{section_title(section)} — select an action:",
            reply_markup=section_menu_keyboard(section),
            parse_mode=ParseMode.MARKDOWN,
        )
        return True

    # ── Quick status card ──
    elif data == "quick_status":
        qs = admin_health.quick_status()
        text = admin_health.format_quick_status(qs)
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard(
                "quick_status",
                extra=[("🚨 Full Health", "health"), ("🖥️ Status", "status")],
            ),
        )
        return True

    # ── Health dashboard ──
    elif data == "health":
        await query.edit_message_text("⏳ Running health checks...")
        data_dict = admin_health.health_dashboard()
        text = admin_health.format_health_dashboard(data_dict)
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard(
                "health",
                extra=[("🖥️ Status", "status"), ("📊 Quick", "quick_status")],
            ),
        )
        return True

    # ── v4: Top processes ──
    elif data == "top":
        await query.edit_message_text("⏳ Gathering processes...")
        result = admin_server.top_processes(limit=10, sort_by="cpu")
        await query.edit_message_text(
            admin_server.format_top(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("top", extra=[("🧠 By Mem", "top_mem")]),
        )
        return True
    elif data == "top_mem":
        await query.edit_message_text("⏳ Gathering processes...")
        result = admin_server.top_processes(limit=10, sort_by="mem")
        await query.edit_message_text(
            admin_server.format_top(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("top_mem", extra=[("🔥 By CPU", "top")]),
        )
        return True

    # ── v4: Uptime ──
    elif data == "uptime":
        await query.edit_message_text("⏳ Checking uptime...")
        result = admin_server.uptime_report()
        await query.edit_message_text(
            admin_server.format_uptime(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("uptime"),
        )
        return True

    # ── v4: Network ──
    elif data == "net":
        await query.edit_message_text("⏳ Scanning network...")
        result = admin_server.network_report()
        await query.edit_message_text(
            admin_server.format_network(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("net"),
        )
        return True

    # ── v4: SSL certs ──
    elif data == "certs":
        await query.edit_message_text("⏳ Checking certificates...")
        result = admin_server.cert_expiry()
        await query.edit_message_text(
            admin_server.format_certs(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("certs"),
        )
        return True

    # ── v4: Services ──
    elif data == "services":
        await query.edit_message_text("⏳ Listing services...")
        result = admin_server.services_report(filter_str="")
        await query.edit_message_text(
            admin_server.format_services(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("services"),
        )
        return True

    # ── Alert help ──
    elif data == "alert_help":
        await query.edit_message_text(
            "🔔 *Send Alert*\n\n"
            "Usage: `/alert <message>`\n"
            "Usage: `/alert critical <message>`\n\n"
            "Levels: `info` (default), `warning`, `critical`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("menu_ops"),
        )
        return True

    # ── Status ──
    elif data == "status":
        s = admin_ops.system_status()
        text = admin_ops.format_status(s)
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                        InlineKeyboardButton("📊 Health", callback_data="alerts"),
                    ],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
        )
        return True

    # ── Disk ──
    elif data == "disk":
        await query.edit_message_text("⏳ Checking disk usage...")
        result = admin_ops.disk_usage()
        text = admin_ops.format_disk(result)
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 Refresh", callback_data="disk"),
                        InlineKeyboardButton("🧠 Memory", callback_data="mem"),
                    ],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
        )
        return True

    # ── Memory ──
    elif data == "mem":
        await query.edit_message_text("⏳ Gathering memory report...")
        result = admin_ops.memory_report()
        text = admin_ops.format_memory(result)
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 Refresh", callback_data="mem"),
                        InlineKeyboardButton("💾 Disk", callback_data="disk"),
                    ],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
        )
        return True

    # ── Chain status ──
    elif data == "chain":
        await query.edit_message_text("⏳ Checking chain indexer status...")
        result = admin_ops.chain_status()
        if result["ok"]:
            await query.edit_message_text(
                result["output"][:3500],
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("🔄 Refresh", callback_data="chain"),
                            InlineKeyboardButton("🐳 Docker", callback_data="docker"),
                        ],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )
        else:
            await query.edit_message_text(
                f"❌ {result['error']}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("main"),
            )
        return True

    # ── Alerts / Health ──
    elif data == "alerts":
        data_dict = admin_health.health_dashboard()
        text = admin_health.format_health_dashboard(data_dict)
        await query.edit_message_text(
            text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard(
                "health",
                extra=[("🖥️ Status", "status"), ("📊 Quick", "quick_status")],
            ),
        )
        return True

    # ── Quick ──
    elif data == "quick":
        await query.edit_message_text(
            "⚡ *Quick Operations*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔄 Restart Backend", callback_data="docker_restart_rmi-backend"),
                        InlineKeyboardButton("🔄 Restart Bot", callback_data="docker_restart_rugmunchbot"),
                    ],
                    [
                        InlineKeyboardButton("🖥️ Full Status", callback_data="status"),
                        InlineKeyboardButton("📊 Health Check", callback_data="alerts"),
                    ],
                    [
                        InlineKeyboardButton("🐳 Docker PS", callback_data="docker_ps"),
                        InlineKeyboardButton("📦 Backup Status", callback_data="backup_status"),
                    ],
                    [
                        InlineKeyboardButton("💾 Disk", callback_data="disk"),
                        InlineKeyboardButton("🧠 Memory", callback_data="mem"),
                    ],
                    [
                        InlineKeyboardButton("📁 Git Status", callback_data="git_menu"),
                        InlineKeyboardButton("📤 Push All", callback_data="push_all"),
                    ],
                    [
                        InlineKeyboardButton("💻 Terminal", callback_data="term_enter"),
                        InlineKeyboardButton("⬅️ Main Menu", callback_data="main"),
                    ],
                ]
            ),
        )
        return True

    return False
