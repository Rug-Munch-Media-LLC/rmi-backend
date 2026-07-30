#!/usr/bin/env python3
"""Admin Bot configuration — loaded from environment with a strict startup guard."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)


def _get_admin_token() -> str:
    """Fetch admin bot token from gopass at runtime, falling back to env var."""
    try:
        token = (
            subprocess.check_output(
                ["gopass", "show", "rmi/rugmunchbot/admin_bot_token"],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
        if token:
            return token
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    return os.environ.get("ADMIN_BOT_TOKEN", "")


ADMIN_BOT_TOKEN: str = _get_admin_token()
ADMIN_CHAT_ID: int = int(os.environ.get("ADMIN_CHAT_ID", "0"))
RMI_BACKEND_URL: str = os.environ.get("RMI_BACKEND_URL", "http://localhost:8000")

OWNER_IDS: set[int] = {
    int(x.strip()) for x in os.environ.get("TELEGRAM_ALLOWED_USERS", "7075336557").split(",") if x.strip()
}

# ── Startup guard ──────────────────────────────────────────────────────────
if not ADMIN_BOT_TOKEN:
    logger.error("FATAL: ADMIN_BOT_TOKEN environment variable is not set.")
    sys.exit(1)

if ADMIN_CHAT_ID <= 0:
    logger.error("FATAL: ADMIN_CHAT_ID environment variable must be set to a positive integer.")
    sys.exit(1)
