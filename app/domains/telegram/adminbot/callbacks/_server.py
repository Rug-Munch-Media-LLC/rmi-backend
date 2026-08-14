"""
Admin Bot - Callback handlers: server / systemd / reboot
==========================================================
Systemd restart confirmation flow, service list, admin audit log, and the
double-confirmed server reboot flow. Dispatched by
``callbacks._dispatch.handle_callback`` in original chain order. Returns
True when a branch matched.

Note: the ``svc_list`` exact branch is intentionally kept — it is shadowed
by the generic ``svc_*`` branch in ``callbacks._ops`` (which dispatches
before this module), matching the original single-function chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.adminbot import admin_server
from app.domains.telegram.adminbot.helpers import double_confirm_keyboard, refresh_keyboard

try:
    from app.domains.telegram.adminbot import admin_audit
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram import CallbackQuery


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle server/systemd/reboot callback data. Returns True when matched."""
    # ── v4: Restart service (confirmation flow) ──
    if data.startswith("restart_confirm_"):
        svc = data.replace("restart_confirm_", "")
        if admin_audit is not None:
            admin_audit.log_action("service.restart", svc, query.from_user.id)
        result = admin_server.restart_service(svc)
        status_emoji = "✅" if result["ok"] else "❌"
        await query.edit_message_text(
            f"{status_emoji} Restarted `{svc}`\n\n```\n{result.get('output', result.get('error', ''))[:2000]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("services", extra=[("🔧 Services", "services")]),
        )
        return True
    elif data.startswith("restart_cancel"):
        await query.edit_message_text("❌ Restart cancelled.", parse_mode=ParseMode.MARKDOWN)
        return True

    # ── Service list + admin logs ──
    elif data == "svc_list":
        await query.edit_message_text("⏳ Listing services...")
        result = admin_server.services_report(filter_str="")
        await query.edit_message_text(
            admin_server.format_services(result),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=refresh_keyboard("svc_list"),
        )
        return True
    elif data == "admin_logs":
        if admin_audit is None:
            await query.edit_message_text("❌ admin_audit module unavailable.", parse_mode=ParseMode.MARKDOWN)
        else:
            result = admin_audit.recent_actions(limit=20)
            await query.edit_message_text(
                admin_audit.format_admin_logs(result),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("admin_logs"),
            )
        return True

    # ── v5: Reboot (double confirmation) ──
    elif data.startswith("reboot_confirm2_"):
        delay_str = data.replace("reboot_confirm2_", "")
        delay = int(delay_str) if delay_str.isdigit() else 60
        if admin_audit is not None:
            admin_audit.log_action("system.reboot", f"delay={delay}s", query.from_user.id)
        await query.edit_message_text("⏳ Scheduling reboot...")
        result = admin_server.schedule_reboot(delay_seconds=delay)
        status = "✅" if result["ok"] else "❌"
        await query.edit_message_text(
            f"{status} {result.get('output') or result.get('error')}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("🚨 Health", callback_data="health")],
                    [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                ]
            ),
        )
        return True
    elif data.startswith("reboot_confirm_"):
        delay_str = data.replace("reboot_confirm_", "")
        delay = int(delay_str) if delay_str.isdigit() else 60
        await query.edit_message_text(
            f"⛔️ *FINAL CONFIRMATION*\n\n"
            f"This schedules a **reboot** of the server in **{delay}s**.\n"
            f"There is **no undo** once it fires.\n\n"
            f"Are you absolutely sure?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=double_confirm_keyboard("reboot", payload=str(delay), stage=2),
        )
        return True
    elif data.startswith("reboot_cancel"):
        await query.edit_message_text("❌ Reboot cancelled.", parse_mode=ParseMode.MARKDOWN)
        return True

    return False
