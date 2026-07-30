#!/usr/bin/env python3
"""
RugMunchBot -- Inline query handler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram import (
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)

from app.domains.telegram.rugmunchbot.formatters import is_evm, is_sol, short_addr

if TYPE_CHECKING:
    from telegram.ext import ContextTypes


async def handle_inline(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query.query.strip()
    if not query:
        return
    results = []
    if is_evm(query) or is_sol(query):
        results.append(
            InlineQueryResultArticle(
                id="scan_" + query[:10],
                title=f"\U0001f50d Scan {short_addr(query)}",
                description="Run a full security scan on this address",
                input_message_content=InputTextMessageContent(f"/scan {query}"),
            )
        )
    if len(query) >= 2 and len(query) <= 10 and query.isalpha():
        results.append(
            InlineQueryResultArticle(
                id="sym_" + query,
                title=f"\U0001fa99 Lookup ${query.upper()}",
                description="Find token price and info",
                input_message_content=InputTextMessageContent(f"${query.upper()}"),
            )
        )
    if results:
        await update.inline_query.answer(results, cache_time=30, is_personal=True)
