"""
Admin Bot - AI Gateway
=======================
AI model query gateway for the admin bot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.domains.telegram.adminbot.helpers import owner_only

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

try:
    from app.domains.telegram.adminbot import admin_ai
except ImportError:
    admin_ai = None  # type: ignore[assignment]  # optional module


@owner_only
async def cmd_ai(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """AI Gateway — query our AI models."""
    if update.message is None or update.effective_user is None:
        return
    if admin_ai is None:
        await update.message.reply_text("⚠️ AI module not available (missing admin_ai.py)")
        return

    args = ctx.args
    if not args:
        await update.message.reply_text(
            "🤖 *AI Gateway*\n\n"
            "`/ai ask <prompt>` — Ask any AI model (READ-ONLY, no tools)\n"
            "`/ai model` — Switch models\n"
            "`/ai clear` — Clear chat history\n"
            "`/ai status` — Show usage stats\n\n"
            "⚠️ `/ai` runs the model with the read-only agent — no shell/tool access.\n"
            "For tooled (agentic) runs use `/oc` (requires explicit confirmation).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_ai.model_keyboard() if hasattr(admin_ai, "model_keyboard") else None,
        )
        return

    action = args[0].lower()
    user_id = update.effective_user.id

    if action == "model":
        await update.message.reply_text(
            admin_ai.list_models(),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=admin_ai.model_keyboard(),
        )
    elif action == "clear":
        admin_ai.clear_history(user_id)
        await update.message.reply_text("🧹 Chat history cleared.", parse_mode=ParseMode.MARKDOWN)
    elif action == "status":
        usage = admin_ai.get_usage(user_id)
        await update.message.reply_text(
            f"📊 *AI Usage*\n\nModel: `{usage['model']}`\nTokens: {usage['tokens']}\nMessages: {usage['messages']}",
            parse_mode=ParseMode.MARKDOWN,
        )
    elif action in ("ask", "q", "query"):
        prompt = " ".join(args[1:]) if len(args) > 1 else ""
        if not prompt:
            await update.message.reply_text("Usage: `/ai ask <your question>`", parse_mode=ParseMode.MARKDOWN)
            return
        msg = await update.message.reply_text("🤖 Thinking (read-only, no tools)...")
        result = await admin_ai.chat(
            user_id,
            prompt,
            system_prompt="You are an RMI server admin assistant. Be concise and technical. Read-only mode: you cannot run commands or modify anything.",
        )
        if result["ok"]:
            content = result["content"][:3500]
            await msg.edit_text(
                f"🤖 *{result['model']}* ({result['tokens']} tokens)\n\n{content}",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await msg.edit_text(f"❌ AI Error: {result['error'][:200]}", parse_mode=ParseMode.MARKDOWN)
