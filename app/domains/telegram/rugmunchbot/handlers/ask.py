#!/usr/bin/env python3
"""
RugMunchBot -- /ask command: explicit RAG + LLM deep answer.

Unlike the auto-detecting chat tier-1, /ask is an explicit "chat with the
intelligence base" command for deeper questions. It searches all RAG
collections, grounds the LLM with the top hits, and returns one synthesized
answer (no raw chunk echo).

Usage: /ask <question>
Counts against the user's weekly AI-message quota (same gate as chat tier-1)
and the per-second / per-minute flood limit (AI surface).
"""

from __future__ import annotations

from html import escape
from typing import TYPE_CHECKING

from telegram.constants import ParseMode

from app.core.logging import get_logger
from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.ai_chat import chat_synthesis, format_rag_context, search_rag
from app.domains.telegram.rugmunchbot.flood_control import check_flood
from app.domains.telegram.rugmunchbot.formatters import paywall_text
from app.domains.telegram.rugmunchbot.keyboards import back_kb, main_menu_kb, paywall_kb

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = get_logger("rmi_bot.ask")

_SLOW_DOWN = "\U0001f422 <b>Slow down!</b>\n\nYou're sending messages too fast. Please wait {retry}s and try again."


async def cmd_ask(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """RAG + LLM deep answer. Usage: /ask <question>"""
    user = update.effective_user
    if user is None or update.message is None:
        return
    db.get_or_create_user(user.id, user.username, user.first_name)

    query = " ".join(ctx.args or []).strip()
    if not query:
        await update.message.reply_text(
            "\U0001f916 <b>/ask \u2014 Deep AI Answers</b>\n\n"
            "Ask a question and I'll synthesize an answer from the RMI "
            "intelligence base (threat intel, scam patterns, agent memory).\n\n"
            "<b>Examples:</b>\n"
            "\u2022 /ask <code>how do honeypots work</code>\n"
            "\u2022 /ask <code>signs of a rug pull</code>\n"
            "\u2022 /ask <code>what is bundle buying</code>\n\n"
            "\U0001f4a1 Use <code>/research</code> to see raw source documents.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_kb(),
        )
        return

    # Weekly AI quota gate (same as chat tier-1)
    allowed, _used, _limit = db.check_rate_limit(user.id, "ai_msg")
    if not allowed:
        await update.message.reply_text(
            paywall_text(user.id, "ai_msg"),
            parse_mode=ParseMode.HTML,
            reply_markup=paywall_kb(),
        )
        return

    # Flood control (AI surface). /ask always counts — only tier-3 redirects
    # in the free-text handler are refunded.
    flood_ok, retry_after, _receipt = await check_flood(user.id)
    if not flood_ok:
        await update.message.reply_text(
            _SLOW_DOWN.format(retry=retry_after),
            parse_mode=ParseMode.HTML,
        )
        return

    placeholder = await update.message.reply_text(
        "\U0001f50d <b>Researching...</b>\n\n"
        f"\U0001f4ac <i>{query[:200]}</i>\n\n"
        "\u23f3 Searching threat intel + scam knowledge...",
        parse_mode=ParseMode.HTML,
    )

    try:
        hits = await search_rag(query, top_k=5)
        if not hits:
            await placeholder.edit_text(
                "\U0001f50d <b>No Relevant Intelligence</b>\n\n"
                f"No grounded sources found for: <i>{query[:200]}</i>\n\n"
                "<b>Tips:</b>\n"
                "\u2022 Try different keywords\n"
                "\u2022 Include a token address for token-specific questions\n"
                "\u2022 Use <code>/scan &lt;address&gt;</code> for a live audit",
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        context = format_rag_context(hits[:5])
        answer = await chat_synthesis(query, context, user_id=user.id)
        if answer:
            db.increment_usage(user.id, ai_msg=True)
            # LLM output is not trusted HTML — escape it so raw <...> can't
            # crash with Telegram "Can't parse entities".
            await placeholder.edit_text(
                escape(answer[:4000]),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
                disable_web_page_preview=True,
            )
            return

        # LLM synthesis failed/timeout but RAG found grounding — degrade to
        # the best raw hit so the user still gets an answer.
        best_hit = (hits[0].get("content") or "").strip()
        if best_hit:
            db.increment_usage(user.id, ai_msg=True)
            await placeholder.edit_text(
                escape(best_hit[:4000]),
                parse_mode=ParseMode.HTML,
                reply_markup=back_kb(),
            )
            return

        await placeholder.edit_text(
            "\u274c <b>No Answer</b>\n\nThe AI service didn't return a response. Please try again shortly.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
    except Exception as e:
        logger.debug("cmd_ask_error", exc_info=True)
        logger.error("cmd_ask error: %s", e)
        await placeholder.edit_text(
            "\u274c <b>Ask Failed</b>\n\nCould not complete the request. Please try again or use simpler terms.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_kb(),
        )
