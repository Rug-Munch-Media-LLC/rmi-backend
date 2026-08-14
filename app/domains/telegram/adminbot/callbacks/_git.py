"""
Admin Bot - Callback handlers: git & push-all
===============================================
Repo git status menus and the double-confirmed push-all flow.
Dispatched by ``callbacks._dispatch.handle_callback`` in original chain
order. Returns True when a branch matched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_ops
from app.domains.telegram.adminbot.helpers import back_button, double_confirm_keyboard

try:
    from app.domains.telegram.adminbot import admin_audit
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram import CallbackQuery


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle git/push-all callback data. Returns True when matched."""
    # ── Git menu ──
    if data == "git_menu":
        from app.domains.telegram.adminbot.admin_ops import RMI_REPOS

        buttons = []
        row = []
        for repo in RMI_REPOS:
            row.append(InlineKeyboardButton(repo, callback_data=f"git_{repo}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("📤 Push All", callback_data="push_all")])
        buttons.append([InlineKeyboardButton("⬅️ Main Menu", callback_data="main")])

        await query.edit_message_text(
            "📁 *Git Status* — Select a repo:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.MARKDOWN,
        )
        return True

    elif data.startswith("git_"):
        repo = data.replace("git_", "")
        if repo == "menu":
            return True
        await query.edit_message_text(f"⏳ Checking git status for {repo}...")
        result = admin_ops.git_status(repo)
        if result["ok"]:
            await query.edit_message_text(
                result["output"][:3500],
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("git_menu"),
            )
        else:
            await query.edit_message_text(
                f"❌ {result['error']}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=back_button("git_menu"),
            )
        return True

    # ── Push all (DOUBLE confirmation, hardened 2026-08-13) ──
    elif data == "push_all":
        await query.edit_message_text(
            "⚠️ *Confirm push all*\n\n"
            "This pushes **all RugMunch repos** to all remotes "
            "(forgejo / codeberg / gitlab / radicle).\n"
            "You will be asked to confirm **twice** before anything runs.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=double_confirm_keyboard("push"),
        )
        return True
    elif data == "push_confirm":
        await query.edit_message_text(
            "⛔️ *FINAL CONFIRMATION*\n\n"
            "Push all repos to **all 4 remotes**? This updates production "
            "repositories across the fleet.\n\n"
            "Are you absolutely sure?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=double_confirm_keyboard("push", stage=2),
        )
        return True
    elif data == "push_confirm2":
        if admin_audit is not None:
            admin_audit.log_action("git.push.all", "push-all-repos.sh", query.from_user.id)
        await query.edit_message_text("⏳ Pushing all repos...")
        result = admin_ops.push_all_repos()
        output = result.get("output", "") or result.get("error", "")
        status = "✅" if result["ok"] else "❌"
        await query.edit_message_text(
            f"{status} Push all repos ({result.get('duration', 0):.1f}s)\n\n```\n{output[:3000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("git_menu"),
        )
        return True
    elif data == "push_cancel":
        await query.edit_message_text("❌ Push cancelled.", parse_mode=ParseMode.MARKDOWN)
        return True

    return False
