"""
News ingest admin endpoints.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends

from . import verify_admin

logger = logging.getLogger(__name__)

ingest_router = APIRouter()


@ingest_router.get("/ingest/channels")
async def ingest_channels(_=Depends(verify_admin)) -> dict[str, Any]:
    """List configured Telegram monitored channels and their last-read status."""
    from app.domains.news_ingest import ingest as ingest_mod
    from app.domains.news_ingest.scheduler import CHANNELS_ENV, channels_from_env

    channels = channels_from_env()
    env_raw = os.getenv(CHANNELS_ENV, "")
    now = time.monotonic()
    items: list[dict[str, Any]] = []
    for channel in channels:
        username = ingest_mod.channel_username_from_config(channel)
        cooldown_until = ingest_mod._CHANNEL_COOLDOWN_UNTIL.get(username)
        items.append(
            {
                "username": username,
                "category": channel.get("category", "crypto_news"),
                "consecutive_failures": ingest_mod._CHANNEL_READ_FAILURES.get(username, 0),
                "in_cooldown": bool(cooldown_until is not None and now < cooldown_until),
                "cooldown_remaining_seconds": (max(0, int(cooldown_until - now)) if cooldown_until is not None else 0),
            }
        )
    return {
        "channels": items,
        "total": len(items),
        "source": "env" if env_raw.strip() else "default",
        "env_var": CHANNELS_ENV,
    }
