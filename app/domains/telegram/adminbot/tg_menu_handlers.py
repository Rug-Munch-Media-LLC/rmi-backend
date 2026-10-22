"""Wire the Telegram ops console into the admin bot.

Group -1 is the pre-dispatch slot.  Before 2026-10-16 the menu edited the
screen and then *returned*, so the group-0 generic router ran on the same
update and replaced every successful screen with ``🚧 Unhandled action``.
Every branch here that handles a tap now raises
:class:`~telegram.ext.ApplicationHandlerStop`; group 0 only ever sees data this
console does not own.  Edits go through :class:`SafeQuery` so a stale button
(>48h, unchanged text) degrades to a fresh reply instead of an error.
"""

from __future__ import annotations

import html
import logging
from typing import TYPE_CHECKING, Any

from telegram.ext import ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes

from app.domains.telegram.adminbot.callbacks._safe import SafeQuery
from app.domains.telegram.adminbot.helpers import owner_only
from app.domains.telegram.adminbot.tg_menu import CONSOLE_PATTERN, build_menu, find_handler
from app.domains.telegram.adminbot.tg_menu_actions import LeafResult, run_leaf

if TYPE_CHECKING:
    from telegram import Update

logger = logging.getLogger("admin_bot.menu")

MAX_BODY = 3500

MENU_TEXT = (
    "<b>🛠 RugMunch Ops Console</b>\n"
    "Tap a section — every button runs the same helper the shell <code>menu</code> runs.\n"
    "Mutations (restart / backup) ask for a second tap."
)


def _render(result: LeafResult) -> str:
    """HTML-safe body; Telegram caps a message at 4096 characters."""
    body = html.escape(result.text[:MAX_BODY]) or "(no output)"
    return f"<pre>{body}</pre>"


@owner_only
async def cmd_menu(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the ops board (and stop the legacy group-0 /menu twin)."""
    if update.message is None:
        return
    await update.message.reply_text(MENU_TEXT, reply_markup=build_menu(), parse_mode="HTML")
    raise ApplicationHandlerStop


@owner_only
async def on_tap(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Run one tap, edit the message exactly once, then stop propagation."""
    if update.callback_query is None:
        return
    query = SafeQuery(update.callback_query)
    data = query.data or ""
    resolved = find_handler(data)
    running = resolved is not None and resolved[1] not in {"noop", "refresh"}
    await query.answer("Running…" if running else None)

    if resolved is None:
        # The group -1 filter is narrower than the whole app, so this means a
        # console-namespace button was removed from SECTIONS while its data
        # survived in an old message.  Say so once; never silently dead.
        await query.edit_message_text("❓ Unknown button.", reply_markup=build_menu())
        raise ApplicationHandlerStop

    section, action = resolved
    if not running:
        await query.edit_message_text(MENU_TEXT, reply_markup=build_menu(), parse_mode="HTML")
        raise ApplicationHandlerStop

    result = await run_leaf(section, action)
    if not isinstance(result, LeafResult):  # defensive: a leaf contract breach
        result = LeafResult(str(result))
    logger.info("menu tap %s:%s -> %d chars", section, action, len(result.text))
    await query.edit_message_text(
        _render(result),
        reply_markup=result.markup or build_menu(),
        parse_mode="HTML",
    )
    raise ApplicationHandlerStop


def register(app: Any) -> None:
    """Attach the console in the pre-dispatch group, before group 0."""
    app.add_handler(CommandHandler("menu", cmd_menu), group=-1)
    app.add_handler(CallbackQueryHandler(on_tap, pattern=CONSOLE_PATTERN), group=-1)
    logger.info("telegram ops console registered in pre-dispatch group")
