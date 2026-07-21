#!/usr/bin/env python3
"""Admin Bot configuration — loaded from environment with a strict startup guard."""

import os
import sys

ADMIN_BOT_TOKEN: str = os.environ.get("ADMIN_BOT_TOKEN", "")
ADMIN_CHAT_ID: int = int(os.environ.get("ADMIN_CHAT_ID", "0"))
RMI_BACKEND_URL: str = os.environ.get("RMI_BACKEND_URL", "http://localhost:8000")

OWNER_IDS: set[int] = {
    int(x.strip())
    for x in os.environ.get("TELEGRAM_ALLOWED_USERS", "7075336557").split(",")
    if x.strip()
}

# ── Startup guard ──────────────────────────────────────────────────────────
if not ADMIN_BOT_TOKEN:
    print("FATAL: ADMIN_BOT_TOKEN environment variable is not set.", file=sys.stderr)
    sys.exit(1)

if ADMIN_CHAT_ID <= 0:
    print(
        "FATAL: ADMIN_CHAT_ID environment variable must be set to a positive integer.",
        file=sys.stderr,
    )
    sys.exit(1)
