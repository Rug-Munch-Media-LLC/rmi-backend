"""Ghost → ``news_items`` join.

Ghost posts are published content; the news feed reads ``news_items`` only.
This module is the missing edge: it reads the Ghost Content API and upserts
each post as one row with ``source_type='ghost'`` so the same post that lives
at ``https://rugmunch.io/<slug>/`` also appears in ``/api/v1/news/feed``.

Idempotent by construction: ``news_id = 'ghost:<ghost id>'`` and the upsert is
``ON CONFLICT (news_id) DO UPDATE``, so a re-run refreshes title/excerpt/url
and never duplicates a post.

Env (never hardcoded):
  GHOST_CONTENT_API_KEY  — 26-char content key from Ghost Admin → Integrations
  GHOST_CONTENT_API_URL  — e.g. https://rugmunch.io/ghost/api/content
"""

from __future__ import annotations

import html
import os
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

log = get_logger(__name__)

SOURCE = "Ghost"
_BASE = "https://rugmunch.io/ghost/api/content"
_TAGS = re.compile(r"<[^>]+>")
_FIELDS = "id,title,slug,url,excerpt,feature_image,published_at,custom_excerpt,reading_time"


def _content_base() -> str:
    return (os.getenv("GHOST_CONTENT_API_URL") or _BASE).rstrip("/")


def _to_text(raw: str | None) -> str:
    """Ghost excerpt is HTML; the feed wants plain prose."""
    if not raw:
        return ""
    return " ".join(html.unescape(_TAGS.sub(" ", raw)).split())


def _row(post: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Ghost post onto a ``news_items`` record (None = unusable)."""
    post_id = str(post.get("id") or "")
    title = (post.get("title") or "").strip()
    url = (post.get("url") or "").strip()
    if not post_id or not title or not url:
        return None
    published = post.get("published_at") or post.get("created_at")
    if not published:
        return None
    summary = _to_text(post.get("custom_excerpt") or post.get("excerpt"))
    return {
        "news_id": f"ghost:{post_id}",
        "url": url,
        "title": title,
        "summary": summary[:1000],
        "image_url": post.get("feature_image"),
        "source": SOURCE,
        "source_type": "ghost",
        "source_tier": 1,
        "source_trust": 0.9,
        "category": "blog",
        "published_at": datetime.fromisoformat(str(published).replace("Z", "+00:00")).astimezone(UTC),
    }


async def fetch_posts(*, limit: int = 15) -> list[dict[str, Any]]:
    """Read published posts from the Ghost Content API (raises on 4xx/5xx)."""
    key = os.getenv("GHOST_CONTENT_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GHOST_CONTENT_API_KEY is not set")
    params = {"key": key, "limit": str(max(1, min(limit, 100))), "fields": _FIELDS, "order": "published_at desc"}
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(f"{_content_base()}/posts/", params=params)
        resp.raise_for_status()
        return list(resp.json().get("posts") or [])


_UPSERT = """
INSERT INTO news_items (
    news_id, url, title, summary, image_url, source, source_type,
    source_tier, source_trust, category, published_at, ingested_at
) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11, now())
ON CONFLICT (news_id) DO UPDATE SET
    url = EXCLUDED.url,
    title = EXCLUDED.title,
    summary = EXCLUDED.summary,
    image_url = EXCLUDED.image_url,
    published_at = EXCLUDED.published_at,
    ingested_at = now()
"""


async def sync_ghost_posts(pool: Any, *, limit: int = 15) -> dict[str, int]:
    """Upsert Ghost posts into ``news_items``. Returns ``{fetched, upserted, skipped}``."""
    posts = await fetch_posts(limit=limit)
    counts = {"fetched": len(posts), "upserted": 0, "skipped": 0}
    async with pool.acquire() as conn:
        for post in posts:
            row = _row(post)
            if row is None:
                counts["skipped"] += 1
                continue
            await conn.execute(
                _UPSERT,
                row["news_id"],
                row["url"],
                row["title"],
                row["summary"],
                row["image_url"],
                row["source"],
                row["source_type"],
                row["source_tier"],
                row["source_trust"],
                row["category"],
                row["published_at"],
            )
            counts["upserted"] += 1
    log.info("ghost_sync: %s", counts)
    return counts
