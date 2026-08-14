"""
Telegram channel-monitoring news ingestion (read → normalize → dedupe).

Thin ingest layer for monitoring Telegram news channels a few times per day.
Reads recent channel history through the headless Telegram client (never
editing it), normalizes raw messages into ``NewsArticle`` records, and
dedupes on content hash / source URL before handing off to the news pipeline.
"""

from __future__ import annotations

from app.domains.news_ingest.discovery import discover_followed_channels
from app.domains.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,
    dedupe_articles,
    filter_seen_articles,
    mark_articles_ingested,
    read_channel_messages,
    sweep_channels,
)
from app.domains.news_ingest.models import ChannelMessage, NewsArticle
from app.domains.news_ingest.normalize import channel_message_to_article, extract_entities
from app.domains.news_ingest.scheduler import (
    DEFAULT_SWEEPS_PER_DAY,
    daily_sweep_times,
    next_sweep_times,
    sweep_schedule,
)

__all__ = [
    "DEFAULT_DEDUP_COLLECTION",
    "DEFAULT_SWEEPS_PER_DAY",
    "ChannelMessage",
    "NewsArticle",
    "channel_message_to_article",
    "daily_sweep_times",
    "dedupe_articles",
    "discover_followed_channels",
    "extract_entities",
    "filter_seen_articles",
    "mark_articles_ingested",
    "next_sweep_times",
    "read_channel_messages",
    "sweep_channels",
    "sweep_schedule",
]
