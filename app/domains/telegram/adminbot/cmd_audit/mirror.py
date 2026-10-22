"""Dispatch-side audit: denials and commands with no registered handler."""

from __future__ import annotations

from typing import Any

from .install import known_commands
from .record import record_command


def mirror_inbound(user: Any, command: str, args: int) -> None:
    """Record what the handler wrapper cannot see. Never raises."""
    try:
        from app.domains.telegram.adminbot.config import OWNER_IDS

        user_id = getattr(user, "id", None)
        if user_id is not None and user_id not in OWNER_IDS:
            record_command(
                user_id=user_id,
                command=command,
                args_count=args,
                status="denied",
                error_type="NotOwner",
                source="dispatch",
            )
            return
        covered = known_commands()
        if covered and command not in covered:
            record_command(
                user_id=user_id,
                command=command,
                args_count=args,
                status="error",
                error_type="UnknownCommand",
                error_msg="no handler registered for this command",
                source="dispatch",
            )
    except Exception:  # observability must never break dispatch
        return
