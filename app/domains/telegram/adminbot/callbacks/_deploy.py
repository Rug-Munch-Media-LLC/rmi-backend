"""
Admin Bot - Callback handlers: deploy
=======================================
Double-confirmed service deploy flow.
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
    """Handle deploy callback data. Returns True when matched."""
    # ── Deploy (DOUBLE confirmation, hardened 2026-08-13) ──
    if data == "deploy":
        await query.edit_message_text(
            "🚀 *Deploy Services*",
            parse_mode=ParseMode.MARKDOWN,
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
        )
        return True

    elif data.startswith("deploy_cancel"):
        await query.edit_message_text("❌ Deploy cancelled.", parse_mode=ParseMode.MARKDOWN)
        return True

    elif data.startswith("deploy_confirm2_"):
        svc = data.replace("deploy_confirm2_", "")
        if admin_audit is not None:
            admin_audit.log_action("deploy", svc, query.from_user.id)
        await query.edit_message_text(f"⏳ Deploying {svc}...")
        result = admin_ops.deploy_service(svc)
        await query.edit_message_text(
            f"{'✅' if result['ok'] else '❌'} Deploy {svc} ({result.get('duration', 0):.1f}s)\n\n```\n{result.get('output', result.get('error', ''))[:2500]}\n```",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=back_button("deploy"),
        )
        return True

    elif data.startswith("deploy_confirm_"):
        svc = data.replace("deploy_confirm_", "")
        await query.edit_message_text(
            f"⛔️ *FINAL CONFIRMATION*\n\n"
            f"This deploys service **`{svc}`** with `docker compose up -d`.\n"
            f"It mutates the production compose stack — there is **no undo**.\n\n"
            f"Are you absolutely sure?",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=double_confirm_keyboard("deploy", payload=svc, stage=2),
        )
        return True

    elif data.startswith("deploy_"):
        svc = data.replace("deploy_", "")
        await query.edit_message_text(
            f"⚠️ *Confirm deploy*\n\nDeploy service `{svc}`?\n"
            "Deploying mutates the production compose stack — you will be "
            "asked to confirm **twice** before anything runs.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=double_confirm_keyboard("deploy", payload=svc),
        )
        return True

    return False
