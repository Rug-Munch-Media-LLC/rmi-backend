"""
_cmd_docker — DOCKER + DEPLOY commands.

Extracted from ``commands`` (degodding 2026-08-14).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_docker, admin_ops, admin_server
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


# /dlogs with no argument tails the main backend container - the usual question.
_DEFAULT_DLOG = "rmi-backend"


@owner_only
async def cmd_dlogs(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Tail docker logs: /dlogs [container] [lines] (defaults to rmi-backend)."""
    args = ctx.args or []
    container = args[0] if args else _DEFAULT_DLOG
    lines = int(args[1]) if len(args) > 1 and args[1].isdigit() else 50
    note = "" if args else f" (default `{container}`)"
    msg = await update.message.reply_text(f"⏳ Fetching logs for `{container}`...", parse_mode=ParseMode.MARKDOWN)
    result = admin_docker.docker_logs_tail(container, lines)
    if result["ok"] and result["output"]:
        body = admin_ops.clip_text(result["output"], 3500)
        await msg.edit_text(
            f"📜 `{container}`{note} (last {lines} lines):\n\n```\n{body}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("logs_menu"),
        )
        return
    # Not a container: try the same name as a systemd unit (no shell - a
    # container name from chat never reaches a shell string).
    j_result = admin_server.service_logs(container, lines)
    body = admin_ops.clip_text(j_result.get("output", "") or j_result.get("error", ""), 3500)
    await msg.edit_text(
        f"📜 `{container}`{note} (systemd fallback, last {lines} lines):\n\n```\n{body}\n```",
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
    """Show docker reclaimable space. Prune needs two taps: this -> confirm."""
    msg = await update.message.reply_text("⏳ Checking docker disk usage...")
    result = admin_docker.docker_disk_usage()
    await msg.edit_text(
        admin_docker.format_docker_df(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🧹 Prune… (asks to confirm)", callback_data="dprune"),
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
