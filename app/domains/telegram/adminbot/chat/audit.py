"""Record every privileged action before it runs."""

from __future__ import annotations

import logging

from app.domains.telegram.adminbot.admin_audit import log_action

logger = logging.getLogger("adminbot.chat.audit")


def record(action: str, detail: str, user_id: int | None) -> None:
    """Write an audit entry, never raising into the caller's control flow.

    Auditing must not be able to block the action it describes, so failures are
    logged and swallowed; the audit log itself is append-only.
    """
    try:
        log_action(action, detail, user_id)
    except Exception:  # audit must never break the control path
        logger.warning("audit write failed for %s", action, exc_info=True)
