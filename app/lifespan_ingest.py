"""Scheduled news-ingest starters for lifespan - extracted for the structure gate."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:
    import asyncio

log = get_logger("lifespan.ingest")


async def start_telegram_ingest() -> asyncio.Task[Any] | None:
    """Start the Telegram channel ingest scheduler; returns the task or None."""
    try:
        from app.domains.news_ingest.scheduler import start_ingest_scheduler

        interval = max(5, int(os.getenv("RMI_TG_INGEST_INTERVAL_MINUTES", "15")))
        task = await start_ingest_scheduler(interval, first_delay_seconds=90)
        log.info("tg_news_ingest_background_started interval_min=%d", interval)
        return task
    except Exception as exc:
        log.warning("tg_news_ingest_background_start_failed err=%s", exc)
        return None


async def start_pryscraper_ingest() -> asyncio.Task[Any] | None:
    """Start the Pryscraper news ingest scheduler; returns the task or None."""
    try:
        from app.domains.news_ingest.scheduler import start_pryscraper_scheduler

        interval = max(5, int(os.getenv("RMI_PRY_INGEST_INTERVAL_MINUTES", "30")))
        task = await start_pryscraper_scheduler(interval, first_delay_seconds=120)
        log.info("pryscraper_news_ingest_background_started interval_min=%d", interval)
        return task
    except Exception as exc:
        log.warning("pryscraper_news_ingest_background_start_failed err=%s", exc)
        return None
