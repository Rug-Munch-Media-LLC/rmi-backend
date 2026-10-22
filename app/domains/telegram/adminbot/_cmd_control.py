"""
_cmd_control — deep server-control commands: /pressure /logins /cleanup /digest.

Added 2026-08-23 (server-control expansion). Ops functions live in
``_ops_control``; this module is the Telegram layer only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.adminbot._ops_control import (
    cleanup_plan,
    fleet_digest,
    format_cleanup,
    format_digest,
    format_logins,
    format_pressure,
    login_audit,
    pressure_report,
)
from app.domains.telegram.adminbot.admin_ops import clip_text
from app.domains.telegram.adminbot.helpers import owner_only, refresh_keyboard

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


@owner_only
async def cmd_pressure(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Per-service cgroup memory/CPU pressure + OOM counts — /pressure [svc...]."""
    if update.message is None:
        return
    msg = await update.message.reply_text("⏳ Reading cgroup pressure...")
    result = pressure_report(extra_services=list(ctx.args or []))
    await msg.edit_text(
        clip_text(format_pressure(result)),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("pressure"),
    )


@owner_only
async def cmd_logins(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Active sessions + recent logins + fail2ban jails — /logins."""
    if update.message is None:
        return
    msg = await update.message.reply_text("⏳ Auditing logins...")
    result = login_audit()
    await msg.edit_text(
        clip_text(format_logins(result)),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("logins"),
    )


@owner_only
async def cmd_cleanup(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Reclaimable-space report (dry-run) — /cleanup."""
    if update.message is None:
        return
    msg = await update.message.reply_text("⏳ Sizing reclaimable space...")
    result = cleanup_plan()
    await msg.edit_text(
        clip_text(format_cleanup(result)),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("cleanup"),
    )


@owner_only
async def cmd_digest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """One-shot fleet health summary — /digest."""
    if update.message is None:
        return
    msg = await update.message.reply_text("⏳ Building fleet digest...")
    result = fleet_digest()
    await msg.edit_text(
        clip_text(format_digest(result)),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=refresh_keyboard("digest"),
    )
