"""The Update fields a text handler needs, resolved once.

A Telegram update is only promised to carry one of `message`, `edited_message`
or `channel_post`, so both are Optional. Every chat handler needs the same two
fields, so they are resolved here: the caller gets a guard, never a crash.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram import Message, Update, User

logger = logging.getLogger("adminbot.chat.parts")

_MISSING = "no message or effective_user on update"


@dataclass(frozen=True, slots=True)
class Parts:
    """The resolved sender and message of an inbound update.

    Frozen so a caller cannot swap the message out from under the audit trail.
    """

    message: Message
    user: User

    @property
    def user_id(self) -> int:
        """The sender's id, already known to be present."""
        return self.user.id


def read(update: Update) -> Parts | None:
    """Return the update's message and sender, or None when either is absent.

    None means the update is malformed — a channel post, an inline query, a
    stripped payload. A handler treats that as a no-op; a raw attribute
    access instead raises inside the dispatcher and surfaces as a 500.
    """
    message, user = update.effective_message, update.effective_user
    if message is None or user is None:
        logger.warning("chat_update_malformed reason=%s", _MISSING)
        return None
    return Parts(message=message, user=user)
