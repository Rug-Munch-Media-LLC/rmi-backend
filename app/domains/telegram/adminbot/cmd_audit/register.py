"""One call to wire the command audit + /diag into a running application."""

from __future__ import annotations

import logging
from typing import Any

from telegram.ext import CommandHandler

from .diag import cmd_diag
from .install import install

logger = logging.getLogger("admin_bot.audit")
_FLAG = "_cmd_audit_installed"


def register(app: Any) -> int:
    """Register /diag, then wrap every command handler (idempotent)."""
    if getattr(app, _FLAG, False):
        return 0
    app.add_handler(CommandHandler("diag", cmd_diag))
    wrapped = install(app)
    setattr(app, _FLAG, True)
    logger.info("cmd_audit ready wrapped=%d", wrapped)
    return wrapped
