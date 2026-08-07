"""
Admin Bot - Helper Utilities
=============================
Owner gating, message editing, keyboard builders for the admin bot.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot.config import OWNER_IDS

# ── Rate limiting ──────────────────────────────────────────────────
SPAM_COOLDOWN: float = 2.0  # seconds between commands
_last_msg: dict[int, float] = {}


def is_rate_limited(uid: int, cooldown: float = SPAM_COOLDOWN) -> bool:
    """Check if user is spamming. Returns True if they should be blocked."""
    now = time.time()
    last = _last_msg.get(uid, 0.0)
    if now - last < cooldown:
        return True
    _last_msg[uid] = now
    return False


def rate_limited(func) -> Any | None:
    """Decorator: enforce per-user cooldown before command execution."""

    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any | None:
        uid = update.effective_user.id if update.effective_user else 0
        if is_rate_limited(uid):
            return  # silently drop spam
        return await func(update, ctx)

    return wrapper


if TYPE_CHECKING:
    from telegram.ext import ContextTypes


def is_owner(update: Update) -> bool:
    """Check if the user is a known owner."""
    return update.effective_user.id in OWNER_IDS


def owner_only(func) -> Any | None:
    """Decorator: reject non-owners immediately + rate limiting."""

    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any | None:
        uid = update.effective_user.id if update.effective_user else 0
        # Rate-limit check (silent drop for spam)
        if is_rate_limited(uid):
            return
        # Authorization check
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
    """Categorized main menu — each section expands to a sub-menu."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📊 System", callback_data="menu_system"),
                InlineKeyboardButton("🐳 Docker", callback_data="menu_docker"),
                InlineKeyboardButton("🗄️ Data", callback_data="menu_data"),
            ],
            [
                InlineKeyboardButton("🤖 AI", callback_data="menu_ai"),
                InlineKeyboardButton("🔧 Tools", callback_data="menu_tools"),
                InlineKeyboardButton("⚙️ Ops", callback_data="menu_ops"),
            ],
            [
                InlineKeyboardButton("🔄 Quick Status", callback_data="quick_status"),
                InlineKeyboardButton("🚨 Alerts", callback_data="health"),
            ],
            [InlineKeyboardButton("⚡ Quick Ops", callback_data="quick")],
        ]
    )


# ── Section sub-menus ────────────────────────────────────────────────
_SYSTEM_MENU: list[tuple[str, str]] = [
    ("🖥️ Status", "status"),
    ("🚨 Health Dashboard", "health"),
    ("💾 Disk", "disk"),
    ("🧠 Memory", "mem"),
    ("⏱️ Uptime", "uptime"),
    ("🔝 Top CPU", "top"),
    ("🌐 Network", "net"),
]

_DOCKER_MENU: list[tuple[str, str]] = [
    ("📋 Docker PS", "dps"),
    ("📊 Stats Top", "dstats"),
    ("📜 Container Logs", "logs_menu"),
    ("🧹 Disk Usage", "dprune"),
    ("⚙️ Manage", "docker"),
]

_DATA_MENU: list[tuple[str, str]] = [
    ("📦 Backup", "backup"),
    ("📁 Git Status", "git_menu"),
    ("📡 Chain", "chain"),
    ("📡 Indexer", "indexer"),
    ("🔍 Scan Stats", "scanstats"),
]

_AI_MENU: list[tuple[str, str]] = [
    ("🤖 OpenCode", "oc_help"),
    ("🧠 Models", "ai_menu"),
    ("🧠 RAG Stats", "ragstats"),
    ("📰 News", "news"),
]

_TOOLS_MENU: list[tuple[str, str]] = [
    ("💻 Shell", "shell_help"),
    ("💻 Terminal", "term_enter"),
    ("🔒 SSL Certs", "certs"),
    ("📜 Service Logs", "logs_menu"),
    ("⚡ Quick Ops", "quick"),
]

_OPS_MENU: list[tuple[str, str]] = [
    ("🔧 Services", "services"),
    ("🚀 Deploy", "deploy"),
    ("📜 Logs", "logs_menu"),
    ("📤 Push All", "push_all"),
    ("🔔 Alert", "alert_help"),
]

SECTION_MENUS: dict[str, list[tuple[str, str]]] = {
    "system": _SYSTEM_MENU,
    "docker": _DOCKER_MENU,
    "data": _DATA_MENU,
    "ai": _AI_MENU,
    "tools": _TOOLS_MENU,
    "ops": _OPS_MENU,
}

_SECTION_TITLES: dict[str, str] = {
    "system": "📊 *System*",
    "docker": "🐳 *Docker*",
    "data": "🗄️ *Data & Intelligence*",
    "ai": "🤖 *AI*",
    "tools": "🔧 *Tools*",
    "ops": "⚙️ *Operations*",
}


def section_menu_keyboard(section: str) -> Any:
    """Build a sub-menu keyboard for a category section."""
    rows = SECTION_MENUS.get(section, [])
    buttons = []
    row: list[InlineKeyboardButton] = []
    for label, data in rows:
        row.append(InlineKeyboardButton(label, callback_data=data))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])
    return InlineKeyboardMarkup(buttons)


def section_title(section: str) -> str:
    """Title text for a category section menu."""
    return _SECTION_TITLES.get(section, "📊 Menu")


def back_button(data: str = "main") -> Any:
    """Simple back button keyboard."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=data)]])


def docker_menu() -> Any:
    """Docker management keyboard (legacy alias for the Docker section menu)."""
    return section_menu_keyboard("docker")


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


def confirm_keyboard(action: str, payload: str = "") -> Any:
    """Confirmation keyboard for destructive actions.

    ``action`` is the callback prefix (e.g. ``restart``, ``dprune``);
    ``payload`` is embedded in the confirm callback (e.g. service name).
    """
    confirm_data = f"{action}_confirm_{payload}" if payload else f"{action}_confirm"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm", callback_data=confirm_data),
                InlineKeyboardButton("❌ Cancel", callback_data=f"{action}_cancel"),
            ]
        ]
    )


def refresh_keyboard(callback: str, extra: list[tuple[str, str]] | None = None) -> Any:
    """Refresh + back keyboard used on data views."""
    row = [InlineKeyboardButton("🔄 Refresh", callback_data=callback)]
    if extra:
        for label, data in extra:
            row.append(InlineKeyboardButton(label, callback_data=data))
    rows = [row, [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")]]
    return InlineKeyboardMarkup(rows)
