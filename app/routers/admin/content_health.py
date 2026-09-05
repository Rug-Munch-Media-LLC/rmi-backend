"""Content pipeline health — per-source ingestion stats.

GET /api/v1/admin/content/health (admin key required)

Aggregates Redis-backed ingestion stats (``content:stats:*``, written by
``app.domains.content.content_stats`` helpers) for X/Twitter, Telegram, RSS
and Pryscraper, plus live Qdrant ``news_v2`` collection stats. Every section
fails open to nulls so a broken source never breaks the dashboard.
"""

from __future__ import annotations

import asyncio
import datetime
import os
from typing import Any

from fastapi import APIRouter, Depends

from app.core.logging import get_logger
from app.routers.admin import verify_admin

logger = get_logger(__name__)

content_health_router = APIRouter(prefix="/content", tags=["admin-content-health"])

_NEWS_COLLECTION = "news_v2"


def _qdrant_base() -> str:
    return os.getenv("QDRANT_URL", os.getenv("QDRANT_FALLBACK", "http://rmi-qdrant-v2:6333")).rstrip("/")


async def _qdrant_news_stats() -> dict[str, Any]:
    """Total points, last-24h count and last article timestamp from news_v2."""
    import httpx

    stats: dict[str, Any] = {"total_articles": None, "articles_24h": None, "last_article_at": None}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            info = await client.get(f"{_qdrant_base()}/collections/{_NEWS_COLLECTION}")
            if info.status_code == 200:
                stats["total_articles"] = info.json().get("result", {}).get("points_count")

            since = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=24)).isoformat()
            count = await client.post(
                f"{_qdrant_base()}/collections/{_NEWS_COLLECTION}/points/count",
                json={"filter": {"must": [{"key": "indexed_at", "range": {"gte": since}}]}},
            )
            if count.status_code == 200:
                stats["articles_24h"] = count.json().get("result", {}).get("count")

            scroll = await client.post(
                f"{_qdrant_base()}/collections/{_NEWS_COLLECTION}/points/scroll",
                json={
                    "limit": 1,
                    "with_payload": ["indexed_at"],
                    "order_by": {"key": "indexed_at", "direction": "desc"},
                },
            )
            points = scroll.json().get("result", {}).get("points", []) if scroll.status_code == 200 else []
            if points:
                stats["last_article_at"] = points[0].get("payload", {}).get("indexed_at")
    except Exception as exc:
        logger.debug("content_health_qdrant_stats_failed err=%s", str(exc)[:200])
    return stats


@content_health_router.get("/health", summary="Content Pipeline Health")
async def content_health(_=Depends(verify_admin)) -> dict[str, Any]:
    """Content pipeline ingestion health per source."""
    from app.caching_shield.rss_sources import RSS_FEEDS
    from app.domains.content.content_stats import get_channel_errors, get_source_stats
    from app.domains.news_ingest.scheduler import channels_from_env

    x_stats, tg_stats, rss_stats, pry_stats = await asyncio.gather(
        get_source_stats("x_twitter"),
        get_source_stats("telegram"),
        get_source_stats("rss"),
        get_source_stats("pryscraper"),
    )

    channels = channels_from_env()
    if not channels:
        try:
            from app.domains.content.daily_pipeline import news_channels_from_registry

            channels = news_channels_from_registry()
        except Exception:
            channels = []

    tg_errors = await get_channel_errors()

    return {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "x_twitter": {
            "last_fetch": x_stats["last_fetch"],
            "ingested_24h": x_stats["ingested_24h"],
            "errors": x_stats["errors"],
            "mode": x_stats["mode"] or ("x_api" if os.getenv("TWITTER_BEARER_TOKEN") else "nitter"),
        },
        "telegram": {
            "channels_monitored": len(channels),
            "last_sweep": tg_stats["last_fetch"],
            "ingested_24h": tg_stats["ingested_24h"],
            "errors": tg_stats["errors"],
            "errors_per_channel": tg_errors,
        },
        "rss": {
            "feeds_configured": len(RSS_FEEDS),
            "last_fetch": rss_stats["last_fetch"],
            "ingested_24h": rss_stats["ingested_24h"],
        },
        "pryscraper": {
            "last_scrape": pry_stats["last_fetch"],
            "ingested_24h": pry_stats["ingested_24h"],
        },
        "qdrant_news_v2": await _qdrant_news_stats(),
    }
