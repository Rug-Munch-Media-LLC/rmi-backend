"""
Admin Bot - Callback Query Dispatcher
=======================================
Entry point for all inline keyboard callback routing of the admin bot.

``handle_callback`` (the single exported entry used by ``bot.py``) performs
the owner gate and then routes callback-data prefixes to per-prefix handler
modules in the *exact original if/elif chain order* — order is behavior-
critical (several prefixes shadow each other, e.g. the generic ``svc_*``
branch shadows the exact ``svc_list`` branch).

Each handler module exports ``async def handle(data, query) -> bool`` and
returns True when a branch matched (dispatcher stops), False otherwise.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

import httpx

from app.domains.telegram import access_guard
from app.domains.telegram.adminbot.callbacks import (
    _ai,
    _backup,
    _bot,
    _deploy,
    _docker,
    _git,
    _nav,
    _ops,
    _power,
    _server,
    _term,
)
from app.domains.telegram.adminbot.config import ADMIN_CHAT_ID
from app.domains.telegram.adminbot.helpers import back_button, is_owner, is_private_chat

# Optional modules (fail-open, mirrors the original callbacks.py guards).
try:
    from app.domains.telegram.adminbot import (
        admin_audit,
        admin_bot,
    )
except ImportError:  # pragma: no cover
    admin_audit = None  # type: ignore[assignment]  # optional module
    admin_bot = None  # type: ignore[assignment]  # optional module

try:
    from app.domains.telegram.adminbot import admin_ai
except ImportError:  # pragma: no cover
    admin_ai = None  # type: ignore[assignment]  # optional module

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

logger = logging.getLogger("admin_bot")

# Ordered handlers — MUST preserve the original if/elif chain order.
#
# Original branch positions (single-function chain):
#   1-10  nav/status cards          -> _nav
#   13-18 docker ps/stats/prune     -> _docker
#   19-22 ai/opencode               -> _ai
#   27-32 git + push all            -> _git
#   40-48 shell/svc control         -> _ops
#   11-12, 56-57, 86-88 server/reboot -> _server
#   51-55, 58-59 backups            -> _backup
#   60-64 deploy                    -> _deploy
#   73-76 terminal                  -> _term
#   80-85 bot management            -> _bot
#
# Constraints enforced by this order:
#   - _ops (generic svc_* branch) dispatches BEFORE _server so the exact
#     ``svc_list`` branch stays shadowed (dead), as in the original chain.
#   - _nav dispatches first: all its branches are exact matches (or menu_*)
#     with no prefix overlap against later modules.
HANDLERS = (
    _nav.handle,
    _docker.handle,
    _ai.handle,
    _power.handle,
    _git.handle,
    _ops.handle,
    _server.handle,
    _backup.handle,
    _deploy.handle,
    _term.handle,
    _bot.handle,
)


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline keyboard callbacks to the appropriate action."""
    query = update.callback_query
    if query is None:
        return
    data = query.data
    if data is None:
        return
    await query.answer()

    # DM-only: callbacks from groups/channels are silently ignored.
    if not is_private_chat(update):
        logger.info("cb dropped data=%s reason=non-private chat=%s", data, _chat_type(update))
        return

    if not is_owner(update):
        # Hidden bot: no visible reply (not even "Unauthorized") — the
        # callback is silently dropped; log + owner alert instead.
        logger.warning("cb DENIED data=%s user=%s", data, _user_id(update))
        user = update.effective_user
        chat = update.effective_chat
        await access_guard.deny_admin_attempt(
            bot=ctx.bot,
            owner_chat_id=ADMIN_CHAT_ID,
            bot_name="rmiadminbot",
            surface=f"cb:{data}",
            user_id=user.id if user else None,
            username=user.username if user else None,
            chat_id=chat.id if chat else None,
            chat_type=chat.type if chat else None,
            audit=(admin_audit.log_action if admin_audit is not None else None),
        )
        return

    try:
        for handler in HANDLERS:
            if await handler(data, query):
                logger.info("cb handled data=%s user=%s", data, _user_id(update))
                return
        # NOTHING matched. Before 2026-09-16 this returned silently — the
        # button press appeared dead to the owner ("menu non-functional").
        # Surface it: visible reply + WARNING so dead callback_data is
        # impossible to miss in the journal.
        logger.warning("cb UNHANDLED data=%s user=%s", data, _user_id(update))
        with contextlib.suppress(httpx.HTTPError, OSError, TimeoutError):
            await query.edit_message_text(
                f"🚧 Unhandled action: `{data[:60]}`\n\nNo handler is wired for this button — report to the bot maintainer.",
                reply_markup=back_button("main"),
                parse_mode="Markdown",
            )
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"Callback error: {e} (data={data})")
        with contextlib.suppress(httpx.HTTPError):
            await query.edit_message_text(f"⚠️ Error: {str(e)[:200]}", reply_markup=back_button("main"))


def _user_id(update: Update) -> int | None:
    """Effective user id for structured logs (None-safe)."""
    user = update.effective_user
    return user.id if user else None


def _chat_type(update: Update) -> str | None:
    """Effective chat type for structured logs (None-safe)."""
    chat = update.effective_chat
    return chat.type if chat else None
