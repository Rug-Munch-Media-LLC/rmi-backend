"""Periodic task publishing health and migration events."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .health_publish import publish as publish_health
from .migration_publish import publish_if_complete

logger = logging.getLogger("adminbot.notify")
INTERVAL = 300


async def watch() -> None:
    """Publish health and migration events for the life of the bot.

    Each producer is isolated: a failure in one logs and the loop continues, so
    a transient problem never stops notifications or the bot.
    """
    while True:
        try:
            level, reason = await publish_health()
            if level != "ok":
                logger.info("health %s: %s", level, reason)
        except Exception:  # a producer must not kill the loop
            logger.warning("health publish failed", exc_info=True)
        with contextlib.suppress(Exception):
            if await publish_if_complete():
                logger.info("migration completion published")
        await asyncio.sleep(INTERVAL)
