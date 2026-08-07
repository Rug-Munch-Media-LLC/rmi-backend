#!/usr/bin/env python3
"""
Secure Bot Launcher — Zero Token Leakage
=========================================
Reads bot token from gopass at startup, exports it to a process-local
environment variable that is UNSET immediately after the bot starts.

This ensures the token:
  - Never appears in systemctl status / journalctl output
  - Never appears in /proc/*/environ (unset before long-running loop)
  - Never appears in ps aux (passed only as transient env, not cmdline arg)
  - Lives ONLY in gopass (encrypted git-based password store)

Usage (systemd ExecStart):
  /path/to/venv/bin/python -m app.domains.telegram.secure_launcher <bot_name>

Where <bot_name> is 'rugmunchbot' or 'adminbot'.
"""

from __future__ import annotations

import os
import subprocess
import sys

# Install token redaction filter BEFORE any logging happens
from app.domains.telegram.log_filter import install as install_token_filter, suppress_httpx_urls

install_token_filter()
suppress_httpx_urls()

GOPASS_PATHS = {
    "rugmunchbot": "rmi/rugmunchbot/bot_token",
    "adminbot": "rmi/rugmunchbot/admin_bot_token",
    "rmiadminbot": "rmi/rmiadminbot/bot_token",
}

ENV_KEYS = {
    "rugmunchbot": ["BOT_TOKEN"],
    "adminbot": ["ADMIN_BOT_TOKEN"],
    "rmiadminbot": ["ADMIN_BOT_TOKEN"],
}

MODULES = {
    "rugmunchbot": "app.domains.telegram.rugmunchbot.bot",
    "adminbot": "app.domains.telegram.adminbot.bot",
    "rmiadminbot": "app.domains.telegram.adminbot.bot",
}


def _fetch_from_gopass(gopass_path: str) -> str:
    """Fetch token from gopass. Returns empty string on failure."""
    try:
        token = (
            subprocess.check_output(
                ["gopass", "show", gopass_path],
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            .decode()
            .strip()
        )
        return token
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return ""


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: secure_launcher.py <rugmunchbot|adminbot>", file=sys.stderr)
        sys.exit(1)

    bot_name = sys.argv[1]
    if bot_name not in GOPASS_PATHS:
        print(f"Unknown bot: {bot_name}", file=sys.stderr)
        sys.exit(1)

    # Fetch token from gopass (never from environment)
    token = _fetch_from_gopass(GOPASS_PATHS[bot_name])

    if not token:
        print(f"FATAL: Could not fetch token from gopass for {bot_name}", file=sys.stderr)
        sys.exit(1)

    # Set ONLY the primary env key for this bot
    primary_key = ENV_KEYS[bot_name][0]
    os.environ[primary_key] = token

    # Import and run the bot module (token is now in env for config.py to read)
    module_name = MODULES[bot_name]
    __import__(module_name)
    bot_module = sys.modules[module_name]

    # UNSET the token from environment BEFORE the bot enters its long-running poll loop
    # This ensures /proc/*/environ never shows the token during normal operation
    del os.environ[primary_key]

    # Clear the token from memory (best-effort, Python GC handles the rest)
    token = ""

    # Run the bot's main()
    if hasattr(bot_module, "main"):
        bot_module.main()
    else:
        print(f"FATAL: {module_name} has no main()", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
