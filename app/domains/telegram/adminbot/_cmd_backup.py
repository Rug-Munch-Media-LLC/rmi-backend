"""
_cmd_backup — BACKUP commands.

Extracted from ``commands`` (degodding 2026-08-14).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_ops
from app.domains.telegram.adminbot.helpers import (
    back_button,
    backup_confirm_keyboard,
    owner_only,
    refresh_keyboard,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

# ═══════════════════════════════════════════════════════════════════
# BACKUP
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_backup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    args = ctx.args or []
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
                    [
                        InlineKeyboardButton("🔄 Run Full Backup", callback_data="backup_run"),
                        InlineKeyboardButton("🗄️ DB Backup", callback_data="backup_db"),
                    ],
                    [InlineKeyboardButton("📊 Backup Status", callback_data="backup_status")],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
            parse_mode=ParseMode.MARKDOWN,
        )


@owner_only
async def cmd_backup_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Last backup per type (postgres, clickhouse, qdrant, rag, ...)."""
    msg = await update.message.reply_text("⏳ Checking backup status...")
    result = admin_ops.backup_status()
    await msg.edit_text(
        admin_ops.format_backup_status(result),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("backup_status", extra=[("▶️ Run", "backup_menu")]),
    )


@owner_only
async def cmd_backup_run(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Trigger a backup: /backup_run [type] — requires confirmation."""
    args = ctx.args or []
    btype = args[0].lower() if args else "full"
    known = (
        "full",
        "db",
        "postgres",
        "clickhouse",
        "qdrant",
        "rag",
        "chain-indexer",
        "git",
    )
    if btype not in known:
        await update.message.reply_text(
            "📦 *Run Backup*\n\n"
            "Usage: `/backup_run <type>`\n\n"
            f"Types: `{'`, `'.join(known)}`\n\n"
            "⚠️ Backups are confirmed before they run.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.message.reply_text(
        f"⚠️ *Confirm backup*\n\nTrigger `{btype}` backup now?\n\n"
        "This runs the fleet backup script and may take several minutes.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=backup_confirm_keyboard(btype),
    )


@owner_only
async def cmd_backup_verify(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Check B2 sync status / backup verification."""
    msg = await update.message.reply_text("⏳ Verifying backups...")
    result = admin_ops.backup_verify()
    output = result.get("output", "") or result.get("error", "")
    await msg.edit_text(
        f"{'✅' if result['ok'] else '❌'} Backup verify ({result.get('duration', 0):.1f}s)\n\n```\n{output[:2500]}\n```",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("backup_verify", extra=[("📊 Status", "backup_status")]),
    )


# ═══════════════════════════════════════════════
# v5 — CONTROL PANEL / BOT MANAGEMENT / REBOOT
# ═══════════════════════════════════════════════


@owner_only
async def cmd_admin_backups(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Last backup timestamps + recent backup artifacts — OWNER ONLY."""
    msg = await update.message.reply_text("⏳ Checking backups...")
    result = admin_ops.backup_report()
    await msg.edit_text(
        result["output"],
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("admin_backups", extra=[("▶️ Run Backup", "backup")]),
    )
