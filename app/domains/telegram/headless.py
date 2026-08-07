"""
Headless Telegram Client
========================
Uses Telethon (MTProto) to manage Telegram groups programmatically
without requiring a browser or interactive login.

This is the public facade. Implementation is split across:
  - ``headless_models.py``  — GroupType, GroupConfig, GroupInfo
  - ``headless_credentials.py`` — Credential fetching from gopass
  - ``headless_client.py``  — HeadlessTelegramClient class
"""

from __future__ import annotations

from app.core.logging import get_logger
from app.domains.telegram.headless_client import HeadlessTelegramClient
from app.domains.telegram.headless_models import GroupConfig, GroupInfo, GroupType

logger = get_logger("telegram.headless")

__all__ = [
    "GroupConfig",
    "GroupInfo",
    "GroupType",
    "HeadlessTelegramClient",
    "_client_instance",
    "get_headless_client",
    "shutdown_headless_client",
]


# ── Singleton ───────────────────────────────────────────────────────────

_client_instance: HeadlessTelegramClient | None = None


async def get_headless_client() -> HeadlessTelegramClient:
    """Get or create the singleton headless Telegram client.

    The client is lazily initialized on first use and reused
    across the application lifecycle.
    """
    global _client_instance

    if _client_instance is None:
        _client_instance = HeadlessTelegramClient()
        await _client_instance.start()

    if not _client_instance.is_connected:
        await _client_instance.start()

    return _client_instance


async def shutdown_headless_client() -> None:
    """Gracefully shut down the headless client.

    Should be called during application shutdown.
    """
    global _client_instance

    if _client_instance and _client_instance.is_connected:
        await _client_instance.stop()
        _client_instance = None
        logger.info("Headless client shut down")
