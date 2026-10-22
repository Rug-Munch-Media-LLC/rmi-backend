"""Telegram public-preview news ingestion.

Public path only (``t.me/s/<channel>``): no bot token, no user session, no
joined channels. See :mod:`app.databus.tg_news.scraper` for the bounded,
stateful backfill and :mod:`app.databus.tg_news.databus` for the feed adapter.
"""

from __future__ import annotations

from app.databus.tg_news.classify import annotate, channel_profile, score_post, trust_band
from app.databus.tg_news.databus import telegram_news_articles
from app.databus.tg_news.parse import parse_channel_page
from app.databus.tg_news.pipeline import ingest_all
from app.databus.tg_news.scraper import ingest_channel

__all__ = [
    "annotate",
    "channel_profile",
    "ingest_all",
    "ingest_channel",
    "parse_channel_page",
    "score_post",
    "telegram_news_articles",
    "trust_band",
]
