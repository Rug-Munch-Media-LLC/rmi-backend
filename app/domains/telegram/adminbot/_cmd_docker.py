"""
_cmd_docker — DOCKER + DEPLOY commands.

Extracted from ``commands`` (degodding 2026-08-14).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_docker, admin_ops
from app.domains.telegram.adminbot.helpers import (
    back_button,
    docker_confirm_keyboard,
    docker_menu,
    double_confirm_keyboard,
    owner_only,
    refresh_keyboard,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

# ═══════════════════════════════════════════════════════════════════
# DOCKER
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_docker(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "🐳 *Docker Management*",
        reply_markup=docker_menu(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ═══════════════════════════════════════════════
# NEW v4 — DOCKER MANAGEMENT
# ═══════════════════════════════════════════════


@owner_only
async def cmd_dps(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Formatted docker ps table (name, status, uptime, ports)."""
    msg = await update.message.reply_text("⏳ Listing containers...")
    result = admin_docker.docker_ps_table()
    await msg.edit_text(
        admin_docker.format_docker_ps(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("dps", extra=[("📊 Stats", "dstats")]),
    )


@owner_only
async def cmd_dlogs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Tail docker logs for a container: /dlogs <container> [lines]."""
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "📜 *Docker Logs*\n\n"
            "Usage: `/dlogs <container> [lines]`\n\n"
            "Examples:\n"
            "`/dlogs rmi-backend`\n"
            "`/dlogs infinity-rag 100`\n\n"
            "Default: 50 lines. Max: 500.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    container = args[0]
    lines = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
    msg = await update.message.reply_text(f"⏳ Fetching logs for `{container}`...", parse_mode=ParseMode.MARKDOWN)
    result = admin_docker.docker_logs_tail(container, lines)
    if result["ok"] and result["output"]:
        await msg.edit_text(
            f"📜 `{container}` (last {lines} lines):\n\n```\n{result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )
    else:
        j_result = admin_ops.run_shell(f"journalctl -u {container}.service --no-pager -n {lines} 2>&1", timeout=10)
        await msg.edit_text(
            f"📜 `{container}` (systemd fallback, last {lines} lines):\n\n```\n{j_result['output'][:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )


@owner_only
async def cmd_dstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Docker stats snapshot — top 10 by memory."""
    msg = await update.message.reply_text("⏳ Sampling container stats...")
    result = admin_docker.docker_stats_top(limit=10)
    await msg.edit_text(
        admin_docker.format_docker_stats(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("dstats", extra=[("📋 PS", "dps")]),
    )


@owner_only
async def cmd_drestart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Restart a docker container — requires confirmation."""
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "🔄 *Restart Container*\n\n"
            "Usage: `/drestart <container>`\n\n"
            "Examples:\n"
            "`/drestart rmi-backend`\n"
            "`/drestart rmi-redis`\n\n"
            "⚠️ You will be asked to confirm before the restart executes.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    container = args[0]
    if not all(c.isalnum() or c in "-_" for c in container):
        await update.message.reply_text(f"❌ Invalid container name: `{container}`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text(
        f"⚠️ *Confirm restart*\n\nRestart docker container `{container}`?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=docker_confirm_keyboard(container),
    )


@owner_only
async def cmd_dprune(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Show docker reclaimable space with prune option."""
    msg = await update.message.reply_text("⏳ Checking docker disk usage...")
    result = admin_docker.docker_disk_usage()
    await msg.edit_text(
        admin_docker.format_docker_df(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🧹 Prune Now", callback_data="dprune"),
                    InlineKeyboardButton("📊 Stats", callback_data="dstats"),
                ],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
            ]
        ),
    )


# ═══════════════════════════════════════════════════════════════════
# DEPLOY
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_deploy(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = ctx.args or []
    if not args:
        await update.message.reply_text(
            "🚀 *Deploy Services*",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🖥️ Backend", callback_data="deploy_backend"),
                        InlineKeyboardButton("🤖 Bot", callback_data="deploy_bot"),
                    ],
                    [
                        InlineKeyboardButton("🛡️ Admin Bot", callback_data="deploy_admin"),
                        InlineKeyboardButton("🏗️ Infra", callback_data="deploy_infra"),
                    ],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    service = args[0]
    await update.message.reply_text(
        f"⚠️ *Confirm deploy*\n\nDeploy service `{service}`?\n"
        "Deploying mutates the production compose stack — you will be asked "
        "to confirm **twice** before anything runs.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=double_confirm_keyboard("deploy", payload=service),
    )
