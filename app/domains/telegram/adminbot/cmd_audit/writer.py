"""Append-only JSONL sink for the command audit (never raises)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .rotation import rotate_if_needed

logger = logging.getLogger("admin_bot.audit")

DEFAULT_LOG_PATH = Path("/var/log/rmiadminbot/commands.jsonl")


def log_path() -> Path:
    """Resolve the audit path (env override keeps tests off /var/log)."""
    override = os.environ.get("RMI_ADMINBOT_AUDIT_LOG")
    return Path(override) if override else DEFAULT_LOG_PATH


def append_line(line: str) -> bool:
    """Append one JSONL line, rotating first; False (never raise) on failure."""
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(path)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except Exception:
        logger.debug("audit append failed path=%s", path, exc_info=True)
        return False
