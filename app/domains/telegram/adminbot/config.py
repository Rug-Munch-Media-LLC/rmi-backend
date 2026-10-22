#!/usr/bin/env python3
"""Admin Bot configuration — loaded from environment with a strict startup guard."""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

# Canonical gopass path for the rmiadminbot token (held in the secret store,
# never in systemd Environment= or source control).
GOPASS_PATH = "rmi/rmiadminbot/bot_token"

# Canonical gopass path for the owner/operator Telegram ids. The secret holds
# the comma-separated ids (e.g. hermes/keys/env/telegram_allowed_users) so no
# id is ever baked into source control.
GOPASS_ALLOWED_USERS_PATH = "hermes/keys/env/telegram_allowed_users"


def _get_admin_token() -> str:
    """Fetch admin bot token from gopass at runtime, falling back to env var."""
    try:
        token = (
            subprocess.check_output(["gopass", "show", GOPASS_PATH], stderr=subprocess.DEVNULL, timeout=5, shell=False)
            .decode()
            .strip()
        )
        if token:
            return token
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.debug("gopass token fetch failed, falling back to env: %s", e)
    return os.environ.get("ADMIN_BOT_TOKEN", "")


ADMIN_BOT_TOKEN: str = _get_admin_token()
ADMIN_CHAT_ID: int = int(os.environ.get("ADMIN_CHAT_ID", "0"))
RMI_BACKEND_URL: str = os.environ.get("RMI_BACKEND_URL", "http://localhost:8000")


def _parse_ids(raw: str) -> set[int]:
    """Parse a comma-separated list of numeric ids; garbage tokens are dropped."""
    ids: set[int] = set()
    for chunk in (raw or "").split(","):
        token = chunk.strip()
        if not token:
            continue
        try:
            ids.add(int(token))
        except ValueError:
            logger.warning("Ignoring non-numeric owner id token: %r", token)
    return ids


def _get_allowed_users() -> set[int]:
    """Resolve owner/operator Telegram ids — env first, gopass fallback.

    Resolution order (fail-closed, NEVER hardcoded):
      1. TELEGRAM_ALLOWED_USERS  (comma-separated ids, env-only)
      2. RMI_OWNER_ID            (single id, env-only)
      3. gopass secret           (GOPASS_ALLOWED_USERS_PATH)
    An empty result trips the startup guard below: the bot refuses to start
    rather than trust a baked-in id.
    """
    ids = _parse_ids(os.environ.get("TELEGRAM_ALLOWED_USERS", ""))
    if ids:
        return ids

    ids = _parse_ids(os.environ.get("RMI_OWNER_ID", ""))
    if ids:
        return ids

    try:
        stored = (
            subprocess.check_output(
                ["gopass", "show", GOPASS_ALLOWED_USERS_PATH],
                stderr=subprocess.DEVNULL,
                timeout=5,
                shell=False,
            )
            .decode()
            .strip()
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        stored = ""
    return _parse_ids(stored)


# Owner/operator ids — resolved from env/gopass only (see _get_allowed_users).
# An empty set fails the startup guard below (fail-closed) instead of silently
# trusting a baked-in id.
OWNER_IDS: set[int] = _get_allowed_users()


# ── Startup guard ──────────────────────────────────────────────────────────
# Callable, NOT module-level: importing this module must never exit (tests,
# tooling, CI collection). The bot entrypoint calls validate_startup() before
# polling; fail-closed semantics are unchanged at runtime.
def validate_startup() -> None:
    """Exit with a logged error when required admin config is missing."""
    if not ADMIN_BOT_TOKEN:
        logger.error("FATAL: ADMIN_BOT_TOKEN environment variable is not set.")
        sys.exit(1)
    if ADMIN_CHAT_ID <= 0:
        logger.error("FATAL: ADMIN_CHAT_ID environment variable must be set to a positive integer.")
        sys.exit(1)
    if not OWNER_IDS:
        logger.error("FATAL: TELEGRAM_ALLOWED_USERS environment variable must list at least one owner id.")
        sys.exit(1)


# Keep Telegram's slash-command picker curated. The bot still accepts the
# legacy aliases below, but exposing every historical alias made the picker
# look like a broken command catalog.
CANONICAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Open the control panel"),
    ("help", "Show the control panel"),
    ("menu", "Open the interactive menu"),
    ("commands", "List supported commands"),
    ("status", "Host status"),
    ("health", "Fleet health dashboard"),
    ("disk", "Disk usage"),
    ("mem", "Memory usage"),
    ("top", "Top CPU processes"),
    ("uptime", "Uptime and load"),
    ("docker", "Docker management"),
    ("dps", "Docker containers"),
    ("services", "Fleet services"),
    ("svc", "Service control"),
    ("logs", "Service logs"),
    ("backup", "Backup status"),
    ("ragstats", "RAG statistics"),
    ("news", "News feed"),
    ("scanstats", "Scanner statistics"),
    ("indexer", "Indexer status"),
    ("ai", "Read-only AI query"),
    ("models", "Choose an AI model"),
    ("engine", "Choose an interaction engine"),
    ("ask", "Run the selected engine"),
    ("oc", "Confirmed tooled OpenCode run"),
    ("term", "Enter terminal mode"),
    ("diag", "Diagnose every command"),
    ("selftest", "Run bot self-test"),
)
