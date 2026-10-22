"""BadRequest-safe callback queries.

Telegram refuses an edit when the message is older than 48h
(``message can't be edited`` / ``message is too old``), when the content is
unchanged (``message is not modified``), or when the source is not a text
message.  An inline button can be tapped days after it was drawn, so every
callback gets a :class:`SafeQuery` proxy: edits fall back to a fresh reply,
every other attribute is delegated untouched.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any

from telegram.error import BadRequest

if TYPE_CHECKING:
    from telegram import CallbackQuery

logger = logging.getLogger("admin_bot")


async def _reply(message: Any, text: str, kwargs: dict[str, Any]) -> None:
    """Reply, tolerating fake/partial message objects and a rejected markup."""
    try:
        sent = message.reply_text(text, **kwargs)
        if inspect.isawaitable(sent):
            await sent
    except (BadRequest, TypeError):
        sent = message.reply_text(text)
        if inspect.isawaitable(sent):
            await sent


async def safe_edit(query: CallbackQuery, text: str, **kwargs: Any) -> None:
    """Edit the callback message, or reply with a new one if Telegram refuses."""
    try:
        await query.edit_message_text(text, **kwargs)
        return
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        logger.info("edit refused (%s) — replying instead", str(exc)[:90])
    message = getattr(query, "message", None)
    if message is None:
        logger.warning("edit refused and no message to reply to")
        return
    await _reply(message, text, kwargs)


class SafeQuery:
    """CallbackQuery proxy whose edits never die on a stale message."""

    def __init__(self, query: CallbackQuery) -> None:
        object.__setattr__(self, "_query", query)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_query"), name)

    async def edit_message_text(self, text: str, **kwargs: Any) -> Any:
        await safe_edit(object.__getattribute__(self, "_query"), text, **kwargs)
        return getattr(object.__getattribute__(self, "_query"), "message", None)
