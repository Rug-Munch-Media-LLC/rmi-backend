"""Wire the Telegram-native menu into the admin bot.

Adds /menu and a callback router. Both are owner-gated by the bot's existing
decorator, so this adds capability without widening access.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from app.domains.telegram.adminbot.helpers import owner_only
from app.domains.telegram.adminbot.tg_menu import build_menu, find_handler, run_action

if TYPE_CHECKING:
    from telegram import Update

logger = logging.getLogger("admin_bot.menu")

MENU_TEXT = (
    "<b>🛠 RugMunch Server Console</b>\n"
    "Tap a button — same actions as the shell <code>menu</code>.\n"
    "Everything runs on the build box; nothing here mutates the terminal box."
)


async def _run(command: str) -> str:
    """Run a shell command without letting it kill the update loop."""
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=25)
    except TimeoutError:
        proc.kill()
        return "⏱️ Timed out after 25s."
    text = out.decode(errors="replace").strip()
    return text or "(no output)"


@owner_only
async def cmd_menu(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    """Render the inline server-management board."""
    if update.message is None:
        return
    await update.message.reply_text(MENU_TEXT, reply_markup=build_menu())


@owner_only
async def on_tap(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route a button press to its action and replace the board in place."""
    query: Any = update.callback_query
    await query.answer()
    resolved = find_handler(query.data or "")
    if resolved is None:
        await query.edit_message_text("❓ Unknown button.")
        return
    namespace, action = resolved
    if resolved == ("menu", "refresh") or resolved == ("menu", "noop"):
        await query.edit_message_text(MENU_TEXT, reply_markup=build_menu())
        return
    await query.edit_message_text("⏳ Running…")
    try:
        text = await asyncio.wait_for(run_action(namespace, action, _run), timeout=40)
    except TimeoutError:
        text = "⏱️ Timed out."
    except Exception as exc:  # user sees a message; the traceback stays in the log
        logger.exception("menu action %s:%s failed", namespace, action)
        text = f"❌ {type(exc).__name__}: {str(exc)[:200]}"
    await query.edit_message_text(f"<pre>{text}</pre>", reply_markup=build_menu())


def register(app: Any) -> None:
    """Attach menu handlers before the legacy generic command/callback router."""
    # The legacy dispatcher is registered in group 0 and consumes every
    # callback first.  Put the native menu in the pre-dispatch group so its
    # callback data is handled exactly once instead of becoming "unhandled".
    app.add_handler(CommandHandler("menu", cmd_menu), group=-1)
    app.add_handler(CallbackQueryHandler(on_tap, pattern=r"^(ai|ops|srv|fleet|menu):"), group=-1)
    logger.info("telegram menu registered in pre-dispatch group")
