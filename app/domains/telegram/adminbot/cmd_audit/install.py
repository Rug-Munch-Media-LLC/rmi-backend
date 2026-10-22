"""Single-place installation: wrap every registered CommandHandler once."""

from __future__ import annotations

import logging
from typing import Any

from telegram.ext import CommandHandler

from .wrap import audit_call

logger = logging.getLogger("admin_bot.audit")

KNOWN: set[str] = set()


def known_commands() -> frozenset[str]:
    """Command names the audit wrapper actually covers."""
    return frozenset(KNOWN)


def install(app: Any) -> int:
    """Wrap every command callback in place; return how many were wrapped."""
    handlers = getattr(app, "handlers", {}) or {}
    wrapped = 0
    for group_handlers in list(handlers.values()):
        for handler in list(group_handlers):
            if not isinstance(handler, CommandHandler):
                continue
            commands = tuple(handler.commands or ())
            KNOWN.update(commands)
            name = sorted(commands)[0] if commands else "unknown"
            wrapped_cb = audit_call(name, handler.callback)
            if wrapped_cb is not handler.callback:
                handler.callback = wrapped_cb
                wrapped += 1
    return wrapped
