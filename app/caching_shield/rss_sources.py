"""Crypto RSS feed sources — fallback content tier when X API and nitter are unavailable.

Fetches headlines from major crypto news outlets and normalizes them into the
same record shape as `get_twitter_feed` so downstream consumers can treat them
uniformly.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from defusedxml import ElementTree as SafeET

from app.core.logging import get_logger

logger = get_logger("rss_sources")

RSS_FEEDS = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "CryptoSlate", "url": "https://cryptoslate.com/feed/"},
    {"name": "The Block", "url": "https://www.theblock.co/rss.xml"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
]

TTL_RSS = 900  # 15 min


def _parse_pubdate(raw: str) -> str:
    """Normalize RSS pubDate to ISO-8601; return raw string on parse failure."""
    try:
        return parsedate_to_datetime(raw).astimezone(UTC).isoformat()
    except (TypeError, ValueError):
        return raw


def _article_record(feed_name: str, title: str, link: str, published: str) -> dict[str, Any]:
    return {
        "id": hashlib.sha256(link.encode()).hexdigest()[:12],
        "text": title,
        "author": feed_name,
        "handle": feed_name.lower().replace(" ", ""),
        "followers": "",
        "pfp": "",
        "url": link,
        "published": published,
        "source": "rss",
        "type": "article",
    }


async def fetch_rss_feeds(limit: int = 30) -> list[dict[str, Any]]:
    """Fetch and parse crypto RSS feeds as a fallback content source.

    Returns articles in the same format as get_twitter_feed records.
    """
    articles: list[dict[str, Any]] = []
    fetch_errors = 0
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for feed in RSS_FEEDS:
            try:
                r = await client.get(feed["url"])
                if r.status_code != 200:
                    logger.warning("rss_fetch_failed", extra={"feed": feed["name"], "status": r.status_code})
                    fetch_errors += 1
                    continue
                root = SafeET.fromstring(r.text)
                for item in root.findall(".//item")[:10]:
                    title_el = item.find("title")
                    link_el = item.find("link")
                    pub_el = item.find("pubDate")
                    title = (title_el.text or "").strip() if title_el is not None else ""
                    link = (link_el.text or "").strip() if link_el is not None else ""
                    if not title or not link:
                        continue
                    published = _parse_pubdate((pub_el.text or "") if pub_el is not None else "")
                    articles.append(_article_record(feed["name"], title, link, published))
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.warning("rss_fetch_error", extra={"feed": feed["name"], "error": str(exc)})
                fetch_errors += 1
                continue

    articles.sort(key=lambda a: a.get("published", ""), reverse=True)

    try:
        from app.domains.content.content_stats import record_fetch

        await record_fetch("rss", errors=fetch_errors)
    except Exception as exc:  # stats are best-effort
        logger.debug("content_stats_write_failed err=%s", str(exc)[:160])

    return articles[:limit]


async def get_crypto_news_rss(limit: int = 30) -> dict[str, Any]:
    """Get crypto news directly from RSS feeds (primary news source, not a fallback)."""
    articles = await fetch_rss_feeds(limit)
    try:
        from app.domains.content.content_stats import record_fetch

        await record_fetch("rss", ingested=len(articles))
    except Exception as exc:  # stats are best-effort
        logger.debug("content_stats_write_failed err=%s", str(exc)[:160])
    return {
        "articles": articles,
        "feeds": [f["name"] for f in RSS_FEEDS],
        "updated": datetime.now(UTC).isoformat(),
    }
