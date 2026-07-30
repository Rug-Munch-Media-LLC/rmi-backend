"""
Admin Bot - Helper Utilities
=============================
Owner gating, message editing, keyboard builders for the admin bot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot.config import OWNER_IDS

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


def is_owner(update: Update) -> bool:
    """Check if the user is a known owner."""
    return update.effective_user.id in OWNER_IDS


def owner_only(func) -> Any | None:
    """Decorator: reject non-owners immediately."""

    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any | None:
        if not is_owner(update):
            await update.message.reply_text("⛔ Unauthorized. This bot is for RMI admins only.")
            return
        return await func(update, ctx)

    return wrapper


async def edit_or_reply(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    text: str,
    keyboard: Any = None,
    parse_mode: str = ParseMode.MARKDOWN,
) -> None:
    """Edit the callback message or send new reply."""
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=parse_mode)
        except (ConnectionError, OSError, TimeoutError):
            await update.callback_query.message.reply_text(text, reply_markup=keyboard, parse_mode=parse_mode)
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=parse_mode)


def main_menu_keyboard() -> Any:
    """Main menu inline keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🐳 Docker", callback_data="docker"),
                InlineKeyboardButton("🖥️ Status", callback_data="status"),
            ],
            [
                InlineKeyboardButton("💻 Shell", callback_data="shell_help"),
                InlineKeyboardButton("🔧 Services", callback_data="services"),
            ],
            [
                InlineKeyboardButton("💾 Disk", callback_data="disk"),
                InlineKeyboardButton("🧠 Memory", callback_data="mem"),
            ],
            [
                InlineKeyboardButton("📦 Backup", callback_data="backup"),
                InlineKeyboardButton("🚀 Deploy", callback_data="deploy"),
            ],
            [
                InlineKeyboardButton("📊 Health", callback_data="alerts"),
                InlineKeyboardButton("📋 Logs", callback_data="logs_menu"),
            ],
            [
                InlineKeyboardButton("📁 Git", callback_data="git_menu"),
                InlineKeyboardButton("📡 Chain", callback_data="chain"),
            ],
            [InlineKeyboardButton("⚡ Quick Ops", callback_data="quick")],
        ]
    )


def back_button(data: str = "main") -> Any:
    """Simple back button keyboard."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=data)]])


def docker_menu() -> Any:
    """Docker management keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📋 List Containers", callback_data="docker_ps"),
                InlineKeyboardButton("📊 Stats", callback_data="docker_stats"),
            ],
            [
                InlineKeyboardButton("🔄 Restart Backend", callback_data="docker_restart_rmi-backend"),
                InlineKeyboardButton("🔄 Restart Postgres", callback_data="docker_restart_rmi-postgres"),
            ],
            [
                InlineKeyboardButton("📋 Compose PS", callback_data="compose_ps"),
                InlineKeyboardButton("🔄 Compose Restart", callback_data="compose_restart"),
            ],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
        ]
    )


def service_action_keyboard(svc: str = "") -> Any:
    """Service control action buttons."""
    buttons = []
    if svc:
        buttons.append(
            [
                InlineKeyboardButton("🔄 Restart", callback_data=f"svc_restart_{svc}"),
                InlineKeyboardButton("⏹ Stop", callback_data=f"svc_stop_{svc}"),
                InlineKeyboardButton("▶️ Start", callback_data=f"svc_start_{svc}"),
            ]
        )
    buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return InlineKeyboardMarkup(buttons)


def logs_menu() -> Any:
    """Logs service selection keyboard."""
    buttons = []
    row = []
    services = [
        "rmi-backend",
        "rmi-postgres",
        "rmi-redis",
        "rmi-clickhouse",
        "rmi-neo4j",
        "rmi-qdrant",
        "rmi-prometheus",
        "rmi-mcp-x402",
    ]
    for _i, svc in enumerate(services):
        row.append(InlineKeyboardButton(svc, callback_data=f"logs_{svc}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return InlineKeyboardMarkup(buttons)


def disk_mem_keyboard() -> Any:
    """Disk/Memory toggle keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💾 Disk", callback_data="disk"),
                InlineKeyboardButton("🧠 Memory", callback_data="mem"),
            ],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
        ]
    )


def git_chain_keyboard() -> Any:
    """Git/Chain quick access keyboard."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📁 Git Status", callback_data="git_menu"),
                InlineKeyboardButton("📡 Chain", callback_data="chain"),
            ],
            [
                InlineKeyboardButton("📤 Push All", callback_data="push_all"),
            ],
            [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
        ]
    )
