"""
Admin Bot - Callback handlers: RugMunchBot management
======================================================
Bot stats/users/broadcasts and toggle flows.
Dispatched by ``callbacks._dispatch.handle_callback`` in original chain
order. Returns True when a branch matched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from app.domains.telegram.adminbot.helpers import bot_toggle_menu_keyboard, refresh_keyboard

try:
    from app.domains.telegram.adminbot import admin_bot
except ImportError:  # pragma: no cover
    admin_bot = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram import CallbackQuery


async def handle(data: str, query: CallbackQuery) -> bool:
    """Handle bot-management callback data. Returns True when matched."""
    # ── v5: Bot management ──
    if data == "bot_stats":
        await query.edit_message_text("⏳ Fetching bot stats...")
        if admin_bot is None:
            await query.edit_message_text("❌ admin_bot module unavailable.", parse_mode=ParseMode.MARKDOWN)
        else:
            stats = await admin_bot.bot_stats()
            await query.edit_message_text(
                admin_bot.format_bot_stats(stats),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton("🔄 Refresh", callback_data="bot_stats"),
                            InlineKeyboardButton("👥 Users", callback_data="bot_users"),
                        ],
                        [InlineKeyboardButton("🔀 Toggle", callback_data="bot_toggle_menu")],
                        [InlineKeyboardButton("⬅️ Main Menu", callback_data="main")],
                    ]
                ),
            )
        return True

    elif data == "bot_users":
        await query.edit_message_text("⏳ Listing users...")
        if admin_bot is None:
            await query.edit_message_text("❌ admin_bot module unavailable.", parse_mode=ParseMode.MARKDOWN)
        else:
            result = admin_bot.bot_users(limit=15)
            await query.edit_message_text(
                result["output"] or result["error"],
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("bot_users", extra=[("🤖 Stats", "bot_stats")]),
            )
        return True

    elif data == "bot_broadcast_help":
        await query.edit_message_text(
            "📬 *Bot Broadcast*\n\n"
            "Usage: `/admin_bot_broadcast <message>`\n"
            "History: `/admin_bot_broadcasts`\n\n"
            "Sends the message to every non-banned bot user (delivered async "
            "by the bot's broadcast worker).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("📜 History", callback_data="bot_broadcasts")],
                    [InlineKeyboardButton("⬅️ Back", callback_data="menu_bots")],
                ]
            ),
        )
        return True

    elif data == "bot_broadcasts":
        await query.edit_message_text("⏳ Fetching broadcast history...")
        if admin_bot is None:
            await query.edit_message_text("❌ admin_bot module unavailable.", parse_mode=ParseMode.MARKDOWN)
        else:
            result = await admin_bot.bot_broadcasts(limit=10)
            await query.edit_message_text(
                result["output"] or result["error"],
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=refresh_keyboard("bot_broadcasts", extra=[("📬 Send", "bot_broadcast_help")]),
            )
        return True

    elif data == "bot_toggle_menu":
        await query.edit_message_text(
            "🔀 *RugMunchBot Toggle*\n\nChoose an action:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=bot_toggle_menu_keyboard(),
        )
        return True

    elif data in ("bot_toggle_on", "bot_toggle_off", "bot_chat_on", "bot_chat_off"):
        enable = data.endswith("_on")
        is_chat = data.startswith("bot_chat")
        label = "chat" if is_chat else "bot"
        await query.edit_message_text(f"⏳ {'Enabling' if enable else 'Disabling'} {label}...")
        if admin_bot is None:
            await query.edit_message_text("❌ admin_bot module unavailable.", parse_mode=ParseMode.MARKDOWN)
        else:
            result = await admin_bot.bot_toggle_chat(enable) if is_chat else await admin_bot.bot_toggle(enable)
            status = "✅" if result["ok"] else "❌"
            await query.edit_message_text(
                f"{status} {result['output'] or result['error']}",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("🤖 Stats", callback_data="bot_stats")],
                        [InlineKeyboardButton("⬅️ Menu", callback_data="menu_bots")],
                    ]
                ),
            )
        return True

    return False
