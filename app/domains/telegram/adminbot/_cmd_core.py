"""
_cmd_core — START/HELP/STATUS/DISK/MEM/SHELL/LOGS/HEALTH/QUICK commands.

Extracted from ``commands`` (degodding 2026-08-14).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_health, admin_ops
from app.domains.telegram.adminbot.helpers import (
    back_button,
    control_panel_keyboard,
    logs_menu,
    main_menu_keyboard,
    owner_only,
    refresh_keyboard,
)

try:
    from app.domains.telegram.adminbot import admin_audit
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

# ═══════════════════════════════════════════════════════════════════
# START / HELP
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    qs = admin_health.control_panel()
    card = admin_health.format_control_panel(qs)
    await update.message.reply_text(
        f"{card}",
        reply_markup=control_panel_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@owner_only
async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, ctx)


@owner_only
async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Show the categorized main menu."""
    await update.message.reply_text(
        "🛡️ *RMI Admin Bot* — Select a section:",
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════
# STATUS
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    msg = await update.message.reply_text("⏳ Gathering fleet status...")
    s = admin_ops.system_status()
    text = admin_ops.format_status(s)
    await msg.edit_text(
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


# ═══════════════════════════════════════════════════════════════════
# DISK
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_disk(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    msg = await update.message.reply_text("⏳ Checking disk usage...")
    result = admin_ops.disk_usage()
    text = admin_ops.format_disk(result)
    await msg.edit_text(
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


# ═══════════════════════════════════════════════════════════════════
# MEMORY / CPU
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_mem(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    msg = await update.message.reply_text("⏳ Gathering memory report...")
    result = admin_ops.memory_report()
    text = admin_ops.format_memory(result)
    await msg.edit_text(
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


# ═══════════════════════════════════════════════════════════════════
# SHELL
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_shell(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "💻 *Shell Execution*\n\n"
            "Usage: `/sh <command>`\n\n"
            "Examples:\n"
            "`/sh df -h`\n"
            "`/sh docker ps`\n"
            "`/sh systemctl status rmi-backend`\n"
            "`/sh journalctl -u rmi-backend -n 50`\n\n"
            "⚠️ READ-ONLY allowlist only (status/logs/ps/ls/cat/df/free).\n"
            "No mutations, no redirection, no pipes. All commands are logged.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    cmd = " ".join(args)
    msg = await update.message.reply_text(f"⏳ Running: `{cmd[:100]}`...", parse_mode=ParseMode.MARKDOWN)
    if admin_audit is not None:
        admin_audit.log_action("shell.exec", cmd[:200], update.effective_user.id if update.effective_user else 0)
    result = admin_ops.run_shell(cmd, timeout=30)
    output = result["output"] or result["error"]
    status_line = f"{'✅' if result['ok'] else '❌'} `{cmd[:80]}` ({result['duration']:.1f}s)"
    await msg.edit_text(
        f"{status_line}\n\n```\n{output[:3000]}\n```",
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════════════════════════
# LOGS
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_logs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = ctx.args or []
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
        j_result = admin_ops.run_shell(f"journalctl -u {service}.service --no-pager -n {tail} 2>&1", timeout=10)
        await msg.edit_text(
            f"📋 `{service}` (systemd, last {tail} lines):\n\n```\n{j_result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )


# ═══════════════════════════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_health(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Health dashboard — runs live fleet health checks.

    Registered as the primary handler for both /health and /alerts
    (legacy alias). The /health name now matches what the command does.
    """
    msg = await update.message.reply_text("⏳ Running health checks...")
    data = admin_health.health_dashboard()
    text = admin_health.format_health_dashboard(data)
    await msg.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard(
            "health",
            extra=[("🖥️ Status", "status"), ("📊 Quick", "quick_status")],
        ),
    )


# Legacy alias — /alerts historically pointed at the health dashboard.
cmd_alerts = cmd_health


# ═══════════════════════════════════════════════════════════════════
# QUICK OPS
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_quick(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "⚡ *Quick Operations*",
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
                    InlineKeyboardButton("⬅️ Main Menu", callback_data="main"),
                ],
            ]
        ),
        parse_mode=ParseMode.MARKDOWN,
    )
