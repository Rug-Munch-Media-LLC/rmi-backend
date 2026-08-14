"""
Admin Bot - Callback handlers: backups
=======================================
Backup run confirm/cancel flows, backup status/verify/menu.
Dispatched by ``callbacks._dispatch.handle_callback`` in original chain
order. Returns True when a branch matched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_ops
from app.domains.telegram.adminbot.helpers import back_button, refresh_keyboard

try:
    from app.domains.telegram.adminbot import admin_audit
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram import CallbackQuery


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle backup callback data. Returns True when matched."""
    # ── Backup run (confirmation flow) ──
    if data.startswith("backup_run_") and data.endswith("_confirm"):
        btype = data[len("backup_run_") : -len("_confirm")]
        if admin_audit is not None:
            admin_audit.log_action("backup.run", btype, query.from_user.id)
        await query.edit_message_text(f"⏳ Running `{btype}` backup...")
        result = admin_ops.trigger_backup(btype)
        await query.edit_message_text(
            f"{'✅' if result['ok'] else '❌'} `{btype}` backup ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("backup"),
        )
        return True
    elif data.startswith("backup_run_") and data.endswith("_cancel"):
        btype = data[len("backup_run_") : -len("_cancel")]
        await query.edit_message_text(f"❌ `{btype}` backup cancelled.", parse_mode=ParseMode.MARKDOWN)
        return True

    # ── Backup status / verify / menu ──
    elif data == "backup_status":
        result = admin_ops.backup_status()
        await query.edit_message_text(
            admin_ops.format_backup_status(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("backup_status", extra=[("▶️ Run", "backup_menu")]),
        )
        return True
    elif data == "backup_verify":
        await query.edit_message_text("⏳ Verifying backups...")
        result = admin_ops.backup_verify()
        output = result.get("output", "") or result.get("error", "")
        await query.edit_message_text(
            f"{'✅' if result['ok'] else '❌'} Backup verify ({result.get('duration', 0):.1f}s)\n\n```\n{output[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("backup_verify", extra=[("📊 Status", "backup_status")]),
        )
        return True
    # ── Backup menu (backup_menu and backup are identical, adjacent) ──
    elif data in ("backup_menu", "backup"):
        await query.edit_message_text(
            "📦 *Backup Control*",
            parse_mode=ParseMode.MARKDOWN,
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
        )
        return True

    elif data in ("backup_run", "backup_db"):
        btype = "full" if data == "backup_run" else "db"
        if admin_audit is not None:
            admin_audit.log_action("backup.run", btype, query.from_user.id)
        await query.edit_message_text(f"⏳ Running {btype} backup...")
        result = admin_ops.trigger_backup(btype)
        await query.edit_message_text(
            f"{'✅' if result['ok'] else '❌'} {btype} backup ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("backup"),
        )
        return True

    return False
