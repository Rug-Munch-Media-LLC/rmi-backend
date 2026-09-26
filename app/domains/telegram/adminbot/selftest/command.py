"""The /selftest command handler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .format import format_selftest
from .keyboard import selftest_keyboard
from .run import run_selftest

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes


async def cmd_selftest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the whole-empire self-test."""
    if update.message is None:
        return
    msg = await update.message.reply_text("🩺 Running self-test…")
    results = await run_selftest()
    await msg.edit_text(format_selftest(results), reply_markup=selftest_keyboard())

