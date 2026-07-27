"""
Group Management Handlers
==========================
Commands for managing creator coin groups, voice/video chats,
and community engagement.

Commands:
  /mygroup — Get link to your creator coin's Telegram group
  /groupinfo — Show group stats (admin only)
  /voicechat — Start/end voice chat in group (admin only)
  /banuser — Ban a user from group (admin only)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot.config import OWNER_IDS

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.groups")

# Voice chat tracking: map user_id -> their group chat_id
_USER_GROUPS: dict[int, int] = {}


async def cmd_mygroup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Get your creator coin Telegram group link.

    If you have a creator coin, this returns the invite link
    for your official Telegram community group.
    """
    user = update.effective_user
    if not user:
        return

    chat_id = _USER_GROUPS.get(user.id)

    if not chat_id:
        # Try to look up from database or headless client
        try:
            from app.domains.telegram.headless import _client_instance

            if _client_instance and hasattr(_client_instance, "_managed_groups"):
                for key, group in _client_instance._managed_groups.items():
                    if hasattr(group, "metadata") and group.metadata.get("creator_user_id") == user.id:
                        chat_id = group.chat_id
                        _USER_GROUPS[user.id] = chat_id
                        break
        except ImportError:
            pass

    if not chat_id:
        await update.message.reply_text(
            "❌ You don't have a creator coin group yet.\n\n"
            "Creator coin groups are automatically created when you "
            "deploy a creator coin on our platform.",
        )
        return

    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        # Force refresh to get current invite link
        try:
            info = await client._fetch_group_info(chat_id)
            invite_link = info.invite_link
            username = info.username
        except Exception:
            invite_link = None
            username = None
    except (ImportError, RuntimeError):
        invite_link = None
        username = None

    if username:
        text = (
            f"🏛️ **Your Creator Coin Group**\n\n"
            f"📢 @{username}\n"
            f"🔗 https://t.me/{username}\n\n"
            f"_Embed this in your profile to show your community!_"
        )
    elif invite_link:
        text = f"🏛️ **Your Creator Coin Group**\n\n🔗 {invite_link}\n\n_Share this link with your community!_"
    else:
        text = (
            "🏛️ **Your Creator Coin Group**\n\n"
            "Your group exists but no invite link is available.\n"
            "Ask an admin to generate one."
        )

    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_groupinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show information about a managed group. Owner/admin only."""
    user = update.effective_user
    if not user or user.id not in OWNER_IDS:
        await update.message.reply_text("⛔ Owner-only command.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /groupinfo <chat_id>\nShows group stats: member count, active voice chats, etc."
        )
        return

    try:
        chat_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat_id. Must be a number.")
        return

    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        info = await client._fetch_group_info(chat_id)
    except (ImportError, RuntimeError, Exception) as e:
        await update.message.reply_text(f"❌ Error: {e}")
        return

    # Check voice chat status
    voice_status = "❌ None active"
    try:
        from app.domains.telegram.rugmunchbot.config import BOT_TOKEN
        from app.domains.telegram.voice_chat import get_voice_chat_status

        if BOT_TOKEN:
            from telegram import Bot

            bot = Bot(token=BOT_TOKEN)
            vc = await get_voice_chat_status(bot, chat_id)
            if vc:
                voice_status = (
                    f"✅ **Active** ({(vc.is_video and '📹 Video') or '🎤 Audio'})\n"
                    f"   Participants: {vc.participant_count}\n"
                    f"   Duration: {vc.duration_seconds:.0f}s"
                )
    except Exception:
        pass

    text = (
        f"🏛️ **Group Info**\n\n"
        f"📌 **Title:** {info.title}\n"
        f"🆔 **Chat ID:** `{info.chat_id}`\n"
        f"👥 **Members:** {info.member_count}\n"
        f"🌐 **Username:** @{info.username or 'N/A'}\n"
        f"🔗 **Invite:** {info.invite_link or 'N/A'}\n"
        f"🔓 **Public:** {'Yes' if info.is_public else 'No'}\n"
        f"🎙️ **Voice Chat:**\n{voice_status}"
    )

    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_voicechat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start or end a voice/video chat. Owner/admin only."""
    user = update.effective_user
    if not user or user.id not in OWNER_IDS:
        await update.message.reply_text("⛔ Owner-only command.")
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "/voicechat start <chat_id> [title]\n"
            "/voicechat start_video <chat_id> [title]\n"
            "/voicechat end <chat_id>"
        )
        return

    action = args[0].lower()
    try:
        chat_id = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat_id.")
        return

    title = " ".join(args[2:]) if len(args) > 2 else None

    from app.domains.telegram.rugmunchbot.config import BOT_TOKEN

    if not BOT_TOKEN:
        await update.message.reply_text("❌ Bot not configured.")
        return

    from telegram import Bot

    bot = Bot(token=BOT_TOKEN)

    if action in ("start", "start_video"):
        is_video = action == "start_video"
        try:
            from app.domains.telegram.voice_chat import create_voice_chat

            session = await create_voice_chat(bot, chat_id, title=title, is_video=is_video)
            link = session.invite_link or ""
            await update.message.reply_text(
                f"✅ {'📹 Video' if is_video else '🎤 Voice'} chat started!\nJoin: {link}"
                if link
                else f"✅ {'📹 Video' if is_video else '🎤 Voice'} chat started!",
                parse_mode="Markdown",
            )
        except RuntimeError as e:
            await update.message.reply_text(f"❌ {e}")
    elif action == "end":
        try:
            from app.domains.telegram.voice_chat import end_voice_chat

            await end_voice_chat(bot, chat_id)
            await update.message.reply_text("✅ Voice chat ended.")
        except RuntimeError as e:
            await update.message.reply_text(f"❌ {e}")
    else:
        await update.message.reply_text("❌ Unknown action. Use: start, start_video, or end.")


async def cmd_banuser(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ban a user from a group. Owner/admin only."""
    user = update.effective_user
    if not user or user.id not in OWNER_IDS:
        await update.message.reply_text("⛔ Owner-only command.")
        return

    args = context.args or []
    if len(args) < 2:
        await update.message.reply_text("Usage: /banuser <chat_id> <user_id> [reason]")
        return

    try:
        chat_id = int(args[0])
        user_id = int(args[1])
    except ValueError:
        await update.message.reply_text("❌ Invalid chat_id or user_id.")
        return

    reason = " ".join(args[2:]) if len(args) > 2 else None

    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        success = await client.ban_user(chat_id, user_id, reason=reason)
        if success:
            await update.message.reply_text(
                f"✅ User `{user_id}` banned from chat `{chat_id}`." + (f"\nReason: {reason}" if reason else ""),
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text("❌ Failed to ban user.")
    except (ImportError, RuntimeError) as e:
        await update.message.reply_text(f"❌ {e}")
