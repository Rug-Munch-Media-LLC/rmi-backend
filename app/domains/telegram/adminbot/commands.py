"""
Admin Bot - Command Handlers
=============================
All /command handler functions for the RMI Admin Bot.
"""

from __future__ import annotations
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.domains.telegram.adminbot import admin_ops
from app.domains.telegram.adminbot.helpers import (
    back_button,
    docker_menu,
    logs_menu,
    main_menu_keyboard,
    owner_only,
    service_action_keyboard,
)

# ── Optional modules ───────────────────────────────────────────────
try:
    from app.domains.telegram.adminbot import admin_ai  # type: ignore[import-untyped]
except ImportError:
    admin_ai = None

try:
    from app.domains.telegram.adminbot import admin_monitor  # type: ignore[import-untyped]
except ImportError:
    admin_monitor = None

try:
    from app.domains.telegram.adminbot import admin_docker  # type: ignore[import-untyped]
except ImportError:
    admin_docker = None

try:
    from app.domains.telegram.adminbot import admin_deploy  # type: ignore[import-untyped]
except ImportError:
    admin_deploy = None

# ═══════════════════════════════════════════════════════════════════
# START / HELP
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🛡️ *RMI Admin Bot v2*\n\n"
        "Server management for the Rug Munch fleet.\n"
        "All commands are owner-only and logged.\n\n"
        "*Commands:*\n"
        "/status — System dashboard\n"
        "/docker — Container management\n"
        "/sh — Shell execution\n"
        "/service — Service control\n"
        "/backup — Backup triggers\n"
        "/deploy — Deploy services\n"
        "/alerts — Health checks\n"
        "/logs — View logs\n"
        "/quick — Common ops\n"
        "/help — This message",
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )

@owner_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)

# ═══════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Gathering fleet status...")
    s = admin_ops.system_status()
    text = admin_ops.format_status(s)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔄 Refresh", callback_data="status"),
                 InlineKeyboardButton("📊 Health", callback_data="alerts")],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )

# ═══════════════════════════════════════════════════════════════════
# DOCKER
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_docker(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🐳 *Docker Management*",
        reply_markup=docker_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )

# ═══════════════════════════════════════════════════════════════════
# SHELL
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_shell(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "💻 *Shell Execution*\n\n"
            "Usage: `/sh <command>`\n\n"
            "Examples:\n"
            "`/sh df -h`\n"
            "`/sh docker ps`\n"
            "`/sh systemctl status rmi-backend`\n\n"
            "⚠️ All commands are logged. Dangerous commands are blocked.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    cmd = " ".join(args)
    msg = await update.message.reply_text(
        f"⏳ Running: `{cmd[:100]}`...", parse_mode=ParseMode.MARKDOWN
    )
    result = admin_ops.run_shell(cmd, timeout=30)
    output = result["output"] or result["error"]
    status_line = f"{'✅' if result['ok'] else '❌'} `{cmd[:80]}` ({result['duration']:.1f}s)"
    await msg.edit_text(
        f"{status_line}\n\n```\n{output[:3000]}\n```",
        parse_mode=ParseMode.MARKDOWN,
    )

# ═══════════════════════════════════════════════════════════════════
# SERVICES
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_service(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        text = admin_ops.service_list()
        await update.message.reply_text(
            text, parse_mode=ParseMode.MARKDOWN, reply_markup=service_action_keyboard()
        )
        return

    name = args[0]
    action = args[1] if len(args) > 1 else "status"

    msg = await update.message.reply_text(f"⏳ {action} {name}...")
    result = admin_ops.service_control(name, action)
    if result["ok"]:
        await msg.edit_text(
            f"✅ `{action} {name}` — done\n\n```\n{result['output'][:2000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=service_action_keyboard(name),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("services"),
        )

# ═══════════════════════════════════════════════════════════════════
# BACKUP
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    action = args[0] if args else ""

    if action == "run":
        msg = await update.message.reply_text("⏳ Running full backup...")
        result = admin_ops.trigger_backup("full")
        await msg.edit_text(
            f"{'✅' if result['ok'] else '❌'} Backup complete ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("backup"),
        )
    elif action == "db":
        msg = await update.message.reply_text("⏳ Running database backup...")
        result = admin_ops.trigger_backup("db")
        await msg.edit_text(
            f"{'✅' if result['ok'] else '❌'} DB Backup ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("backup"),
        )
    else:
        await update.message.reply_text(
            "📦 *Backup Control*",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🔄 Run Full Backup", callback_data="backup_run"),
                     InlineKeyboardButton("🗄️ DB Backup", callback_data="backup_db")],
                    [InlineKeyboardButton("📊 Backup Status", callback_data="backup_status")],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

# ═══════════════════════════════════════════════════════════════════
# DEPLOY
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_deploy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "🚀 *Deploy Services*",
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🖥️ Backend", callback_data="deploy_backend"),
                     InlineKeyboardButton("🤖 Bot", callback_data="deploy_bot")],
                    [InlineKeyboardButton("🛡️ Admin Bot", callback_data="deploy_admin"),
                     InlineKeyboardButton("🏗️ Infra", callback_data="deploy_infra")],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    service = args[0]
    msg = await update.message.reply_text(f"⏳ Deploying {service}...")
    result = admin_ops.deploy_service(service)
    await msg.edit_text(
        f"{'✅' if result['ok'] else '❌'} Deploy {service} ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=back_button("deploy"),
    )

# ═══════════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    args = ctx.args
    if not args:
        await update.message.reply_text(
            "📋 *View Logs*\n\nPick a service:",
            reply_markup=logs_menu(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    service = args[0]
    tail = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
    msg = await update.message.reply_text(f"⏳ Fetching logs for {service}...")
    result = admin_ops.docker_logs(service, tail=tail)
    if result["ok"] and result["output"]:
        await msg.edit_text(
            f"📋 `{service}` (last {tail} lines):\n\n```\n{result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )
    else:
        j_result = admin_ops.run_shell(
            f"journalctl -u {service}.service --no-pager -n {tail} 2>&1", timeout=10
        )
        await msg.edit_text(
            f"📋 `{service}` (systemd, last {tail} lines):\n\n```\n{j_result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )

# ═══════════════════════════════════════════════════════════════════
# ALERTS / HEALTH
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_alerts(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ Running health checks...")
    checks = admin_ops.health_check()
    text = admin_ops.format_health(checks)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔄 Refresh", callback_data="alerts"),
                 InlineKeyboardButton("🖥️ Full Status", callback_data="status")],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )

# ═══════════════════════════════════════════════════════════════════
# QUICK OPS
# ═══════════════════════════════════════════════════════════════════

@owner_only
async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "⚡ *Quick Operations*",
        reply_markup=InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🔄 Restart Backend", callback_data="docker_restart_rmi-backend"),
                 InlineKeyboardButton("🔄 Restart Bot", callback_data="docker_restart_rugmunchbot")],
                [InlineKeyboardButton("🖥️ Full Status", callback_data="status"),
                 InlineKeyboardButton("📊 Health Check", callback_data="alerts")],
                [InlineKeyboardButton("🐳 Docker PS", callback_data="docker_ps"),
                 InlineKeyboardButton("📦 Backup Status", callback_data="backup_status")],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
        parse_mode=ParseMode.MARKDOWN,
    )
