"""
Telegram domain — bot, headless client, alert hooks, and voice chat.
"""

from app.domains.telegram.headless import (
    GroupConfig,
    GroupInfo,
    GroupType,
    HeadlessTelegramClient,
    get_headless_client,
    shutdown_headless_client,
)

__all__ = [
    "GroupConfig",
    "GroupInfo",
    "GroupType",
    "HeadlessTelegramClient",
    "get_headless_client",
    "shutdown_headless_client",
]
