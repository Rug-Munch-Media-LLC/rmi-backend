"""
Admin Bot - Helper Utilities
=============================
Owner gating, message editing, keyboard builders for the admin bot.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatType, ParseMode

from app.domains.telegram import access_guard
from app.domains.telegram.adminbot.config import ADMIN_CHAT_ID, OWNER_IDS

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


def rate_limited(func: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Decorator: enforce per-user cooldown before command execution."""

    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any | None:
        uid = update.effective_user.id if update.effective_user else 0
        if is_rate_limited(uid):
            return None  # silently drop spam
        return await func(update, ctx)

    return wrapper


if TYPE_CHECKING:
    from telegram.ext import ContextTypes


def is_private_chat(update: Update) -> bool:
    """DM-only gate: True only for private chats (never groups/channels)."""
    chat = update.effective_chat
    return chat is not None and chat.type == ChatType.PRIVATE


def is_owner(update: Update) -> bool:
    """Check if the user is a known owner AND the chat is a private DM."""
    if update.effective_user is None:
        return False
    return update.effective_user.id in OWNER_IDS and is_private_chat(update)


def owner_only(func: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Decorator: reject non-owners silently (hidden bot) + rate limiting.

    The admin bot is HIDDEN: non-owners get no reply at all (not even an
    "unauthorized" message), so the bot does not advertise its existence
    or its admin surface. Every denied attempt is logged and a
    rate-limited alert is sent to the owner chat (defense-in-depth).

    DM-only: updates from groups/channels are silently ignored (no alert),
    so the admin surface is reachable exclusively via private chat.
    """

    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> Any | None:
        uid = update.effective_user.id if update.effective_user else 0
        # DM-only gate — non-private chats are ignored outright (no alert).
        if not is_private_chat(update):
            return None
        # Rate-limit check (silent drop for spam)
        if is_rate_limited(uid):
            return None
        # Authorization check — silent deny + owner alert
        if not is_owner(update):
            user = update.effective_user
            chat = update.effective_chat
            surface = f"cmd:{func.__name__}"
            if update.callback_query is not None and update.callback_query.data:
                surface = f"cb:{update.callback_query.data}"
            await access_guard.deny_admin_attempt(
                bot=ctx.bot,
                owner_chat_id=ADMIN_CHAT_ID,
                bot_name="rmiadminbot",
                surface=surface,
                user_id=user.id if user else None,
                username=user.username if user else None,
                chat_id=chat.id if chat else None,
                chat_type=chat.type if chat else None,
                audit=_audit_deny,
            )
            return None
        return await func(update, ctx)

    return wrapper


def _audit_deny(user_id: int, action: str, detail: str = "") -> None:
    """Append a deny record to the admin bot audit trail (fail-open)."""
    try:
        from app.domains.telegram.adminbot import admin_audit

        admin_audit.log_action(action, detail, user_id)
    except Exception:
        import logging

        logging.getLogger("admin_bot").debug("adminbot audit write failed", exc_info=True)


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
            if isinstance(update.callback_query.message, Message):
                await update.callback_query.message.reply_text(text, reply_markup=keyboard, parse_mode=parse_mode)
    else:
        if update.message is not None:
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
        ]
    )


def control_panel_keyboard() -> Any:
    """/start control panel: quick actions + navigation to sub-menus."""
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
        ]
    )


def bot_toggle_menu_keyboard() -> Any:
    """Bot toggle action buttons."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Enable Bot", callback_data="bot_toggle_on"),
                InlineKeyboardButton("🔴 Disable Bot", callback_data="bot_toggle_off"),
            ],
            [
                InlineKeyboardButton("💬 Enable Chat", callback_data="bot_chat_on"),
                InlineKeyboardButton("🔇 Disable Chat", callback_data="bot_chat_off"),
            ],
            [InlineKeyboardButton("⬅️ Back", callback_data="menu_bots")],
        ]
    )


def double_confirm_keyboard(action: str, payload: str = "", stage: int = 1) -> Any:
    """TWO-step confirmation for destructive actions (e.g. reboot).

    ``action`` is the callback prefix. ``stage=1`` produces
    ``{action}_confirm_{payload}``; ``stage=2`` produces
    ``{action}_confirm2_{payload}`` (the final, executing callback).
    """
    suffix = "confirm2" if stage >= 2 else "confirm"
    data = f"{action}_{suffix}_{payload}" if payload else f"{action}_{suffix}"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⚠️ I understand, continue", callback_data=data),
                InlineKeyboardButton("❌ Cancel", callback_data=f"{action}_cancel"),
            ]
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
    ("🩺 Self-Test", "selftest_run"),
]

_DOCKER_MENU: list[tuple[str, str]] = [
    ("📋 Docker PS", "dps"),
    ("📊 Stats Top", "dstats"),
    ("🔄 Restart Container", "drestart_menu"),
    ("📜 Container Logs", "logs_menu"),
    ("🧹 Disk Usage", "dprune"),
    ("⚙️ Manage", "docker"),
]

_DATA_MENU: list[tuple[str, str]] = [
    ("📊 Backup Status", "backup_status"),
    ("🔄 Run Backup", "backup_menu"),
    ("☁️ Verify B2", "backup_verify"),
    ("📁 Git Status", "git_menu"),
    ("📡 Chain", "chain"),
    ("📡 Indexer", "indexer"),
    ("🔍 Scan Stats", "scanstats"),
]

_AI_MENU: list[tuple[str, str]] = [
    ("🤖 OpenCode", "oc_help"),
    ("🎛 Engines", "engines_menu"),
    ("⚡ Heavy Lane", "heavy_status"),
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
    ("🛠 Services", "services"),
    ("🔧 Svc Control", "svc_help"),
    ("📋 Svc List", "svc_list"),
    ("📜 Svc Logs", "svc_logs_menu"),
    ("🚀 Deploy", "deploy"),
    ("📤 Push All", "push_all"),
    ("🔔 Alert", "alert_help"),
]

_BOT_MENU: list[tuple[str, str]] = [
    ("🤖 Bot Stats", "bot_stats"),
    ("👥 Users", "bot_users"),
    ("📬 Broadcast", "bot_broadcast_help"),
    ("🔀 Toggle Bot", "bot_toggle_menu"),
    ("📊 Bot Health", "health"),
]

_ALERTS_MENU: list[tuple[str, str]] = [
    ("🚨 Recent Alerts", "alerts"),
    ("📊 Health Check", "health"),
    ("🧪 Test Alert (E2E)", "test_alert"),
    ("🔔 Alert Help", "alert_help"),
]

SECTION_MENUS: dict[str, list[tuple[str, str]]] = {
    "system": _SYSTEM_MENU,
    "docker": _DOCKER_MENU,
    "data": _DATA_MENU,
    "ai": _AI_MENU,
    "tools": _TOOLS_MENU,
    "ops": _OPS_MENU,
    "bots": _BOT_MENU,
    "alerts": _ALERTS_MENU,
}

_SECTION_TITLES: dict[str, str] = {
    "system": "📊 *System*",
    "docker": "🐳 *Docker*",
    "data": "🗄️ *Data & Intelligence*",
    "ai": "🤖 *AI*",
    "tools": "🔧 *Tools*",
    "ops": "⚙️ *Operations*",
    "bots": "🤖 *RugMunchBot Management*",
    "alerts": "🚨 *Alerts & Monitoring*",
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


def docker_confirm_keyboard(container: str) -> Any:
    """Confirmation keyboard for /drestart <container>."""
    return confirm_keyboard("drestart", payload=container)


def svc_confirm_keyboard(action: str, service: str) -> Any:
    """Confirmation keyboard for /svc <action> <service>."""
    return confirm_keyboard(f"svc_{action}", payload=service)


def backup_confirm_keyboard(btype: str) -> Any:
    """Confirmation keyboard for /backup_run <type>."""
    return confirm_keyboard(f"backup_run_{btype}", payload="")


def refresh_keyboard(callback: str, extra: list[tuple[str, str]] | None = None) -> Any:
    """Refresh + back keyboard used on data views."""
    row = [InlineKeyboardButton("🔄 Refresh", callback_data=callback)]
    if extra:
        for label, data in extra:
            row.append(InlineKeyboardButton(label, callback_data=data))
    rows = [row, [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")]]
    return InlineKeyboardMarkup(rows)
