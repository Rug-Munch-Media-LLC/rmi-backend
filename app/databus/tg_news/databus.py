"""Adapter: stored Telegram posts -> DataBus news articles (no network calls).

The DataBus news cache already accepts generic article dicts (the RSS path
produces the same shape), so this module is the *only* wiring point — the
scraper fills ``data/telegram_news/*.jsonl`` on its own schedule and
``refresh_news_cache`` merges what is on disk.
"""

from __future__ import annotations

import json
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.databus.tg_news.state import news_dir

logger = get_logger("tg_news")

if TYPE_CHECKING:
    from pathlib import Path


def _tail(path: Path, limit: int) -> list[dict[str, Any]]:
    rows: deque[dict[str, Any]] = deque(maxlen=limit)
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    rows.append(json.loads(line))
                except ValueError as exc:
                    logger.debug("skipping corrupt row in %s: %s", path, exc)
                    continue
    except OSError as exc:
        logger.debug("cannot read %s: %s", path, exc)
        return []
    return list(rows)


def _headline(text: str) -> str:
    for line in (text or "").splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned[:140]
    return "(media post)"


def _age_ok(post: dict[str, Any], max_age_hours: float, now: datetime) -> bool:
    try:
        stamp = datetime.fromisoformat(str(post.get("timestamp")).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return now - stamp <= timedelta(hours=max_age_hours)


def telegram_news_articles(
    limit: int = 40,
    *,
    max_age_hours: float = 72.0,
    root: str | None = None,
) -> list[dict[str, Any]]:
    """Newest stored posts across channels as DataBus article dicts."""
    directory = news_dir(root)
    now = datetime.now(UTC)
    articles: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for path in sorted(directory.glob("*.jsonl")) if directory.exists() else []:
        for post in _tail(path, limit):
            if not _age_ok(post, max_age_hours, now):
                continue
            digest = post.get("text_hash")
            if digest and digest in seen_hashes:
                continue
            seen_hashes.add(digest or "")
            channel = str(post.get("channel") or path.stem)
            articles.append(
                {
                    "id": f"tg-{digest or channel + '-' + str(post.get('message_id'))}",
                    "title": _headline(str(post.get("text") or "")),
                    "url": post.get("url") or f"https://t.me/{channel}/{post.get('message_id')}",
                    "description": str(post.get("text") or "")[:300],
                    "source": f"Telegram @{channel}",
                    "published_at": post.get("timestamp") or now.isoformat(),
                    "image_url": None,
                    "category": "news",
                    "sentiment": "neutral",
                    "kind": "external",
                    "tier": "social",
                    "views": post.get("views"),
                    "forward_from": post.get("forward_from"),
                    "channel_kind": post.get("channel_kind"),
                    "kind_weight": post.get("kind_weight"),
                    "trust_score": post.get("trust_score"),
                    "trust_band": post.get("trust_band"),
                }
            )
    articles.sort(key=lambda item: item.get("published_at", ""), reverse=True)
    if not articles:
        return []
    counts = Counter(str(item["source"]) for item in articles)
    for item in articles:
        item["source_post_count"] = counts[str(item["source"])]
    return articles[:limit]
