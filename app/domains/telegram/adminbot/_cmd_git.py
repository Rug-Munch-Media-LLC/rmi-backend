"""
_cmd_git — GIT + PUSH commands.

Extracted from ``commands`` (degodding 2026-08-14).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_ops
from app.domains.telegram.adminbot.helpers import (
    back_button,
    double_confirm_keyboard,
    owner_only,
)

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

# ═══════════════════════════════════════════════════════════════════
# GIT
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_git(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Show git status for a repo — OWNER ONLY."""
    args = ctx.args or []
    if not args:
        # Show repo list menu
        buttons = []
        row = []
        for repo in admin_ops.RMI_REPOS:
            row.append(InlineKeyboardButton(repo, callback_data=f"git_{repo}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("📤 Push All", callback_data="push_all")])
        buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])

        await update.message.reply_text(
            "📁 *Git Status* — Select a repo:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    repo = args[0]
    msg = await update.message.reply_text(f"⏳ Checking git status for {repo}...")
    result = admin_ops.git_status(repo)
    if result["ok"]:
        await msg.edit_text(
            result["output"][:3500],
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("git_menu"),
        )
    else:
        await msg.edit_text(
            f"❌ {result['error']}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("git_menu"),
        )


# ═══════════════════════════════════════════════════════════════════
# PUSH
# ═══════════════════════════════════════════════════════════════════


@owner_only
async def cmd_push(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    """Push all repos — OWNER ONLY. Requires DOUBLE confirmation (hardened 2026-08-13)."""
    await update.message.reply_text(
        "⚠️ *Confirm push all*\n\n"
        "This pushes **all RugMunch repos** to all remotes "
        "(forgejo / codeberg / gitlab / radicle).\n"
        "You will be asked to confirm **twice** before anything runs.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=double_confirm_keyboard("push"),
    )
