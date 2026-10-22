"""Run the public-preview ingestion across the seed/registry channel set."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from app.databus.tg_news.channels import channel_list
from app.databus.tg_news.scraper import PAGE_DELAY, ingest_channel


async def ingest_all(
    channels: list[str] | None = None,
    *,
    limit: int = 200,
    delay: float = PAGE_DELAY,
    root: str | None = None,
) -> dict[str, Any]:
    """Sequentially ingest every selected channel; one failure never aborts the run."""
    seeds = channel_list(channels)
    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
        for index, seed in enumerate(seeds):
            results.append(
                await ingest_channel(
                    client,
                    seed.username,
                    limit=limit,
                    kind_hint=seed.kind_hint,
                    weight=seed.weight,
                    delay=delay,
                    root=root,
                )
            )
            if index < len(seeds) - 1:
                await asyncio.sleep(delay)
    return {
        "ran_at": datetime.now(UTC).isoformat(),
        "channels_requested": len(seeds),
        "channels_ok": sum(1 for row in results if "error" not in row),
        "appended": sum(int(row.get("appended", 0)) for row in results),
        "results": results,
    }
