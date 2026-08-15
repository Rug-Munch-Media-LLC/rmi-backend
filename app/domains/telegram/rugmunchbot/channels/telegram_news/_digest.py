"""Telegram digest formatting + additive feed/market-awareness side-effects.

Submodule of ``channels/telegram_news.py`` — owns the **digest/broadcast
formatting** helpers (``_article_to_post_dict``, ``_category_is_news``) plus
the additive, fail-open Redis side-effects that run after every broadcast:
website full-feed injection (``inject_into_news_cache``) and the lightweight
market-awareness snapshot (``publish_market_awareness``).

``get_redis_async`` is read through the shim at call time so
``patch.object(tg_news, "get_redis_async", ...)`` keeps working.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.domains.news_ingest.ingest import extract_canonical_url, extract_image_url
from app.domains.telegram.rugmunchbot.channels.importance import top_headlines, trending_tokens
from app.domains.telegram.rugmunchbot.channels.telegram_news._persist import _news_id_for
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from app.domains.news_ingest.models import NewsArticle
    from app.domains.telegram.rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

logger = get_logger("rmi_bot.channels.tg_news")


# ── Post to the bot's news channel ───────────────────────────────────────────


def _article_to_post_dict(article: NewsArticle) -> dict[str, Any]:
    """Shape a ``NewsArticle`` for ``format_news_post`` / ``format_breaking_post``.

    ``url`` prefers the canonical source URL (the original article link found
    inside the message) so digests and breaking posts link users to the real
    story instead of the channel's ``t.me`` page. ``image_url`` is populated
    when the article carries a media reference (inline image link, or the
    sweep-populated ``media`` field — og:image / photo reference).
    """
    return {
        "title": article.title,
        "summary": article.body,
        "url": extract_canonical_url(article) or article.source_url,
        "source": article.source_channel,
        "sentiment_label": "",
        "factual_rating": "",
        "image_url": extract_image_url(article) or getattr(article, "media", None) or "",
    }


def _category_is_news(category: str) -> bool:
    """True when a swept article belongs on the news channel (not alpha)."""
    return category != "alpha"


# ── Website feed injection (additive, fail-open) ────────────────────────────


#: The Redis full-feed key the website's ``/api/v1/news/feed`` +
#: ``/api/v1/news/headlines`` endpoints serve (populated by the RSS/API
#: aggregator ``app/databus/news_provider.py``). TG articles are merged into
#: it additively so they surface on the main feed with their channel
#: attribution; the Postgres-backed ``/api/v1/news/trending`` route already
#: reads ``news_items`` directly.
_NEWS_FEED_CACHE_KEY = "rmi:news:full_feed"
_NEWS_FEED_CACHE_MAX = 50
_TG_ARTICLE_KIND = "telegram"


async def _redis_json_get(key: str) -> dict[str, Any] | None:
    """Read a JSON dict from Redis (fail-open → None)."""
    r = _tg.get_redis_async()  # type: ignore[attr-defined]
    if r is None:
        return None
    try:
        raw = await r.get(key)
        if not raw:
            return None
        data = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8", "replace"))
        return data if isinstance(data, dict) else None
    except Exception:  # fail-open by contract
        logger.debug("tg_news:redis_get_failed key=%s", key, exc_info=True)
        return None


async def _redis_json_set(key: str, data: dict[str, Any], ttl: int) -> bool:
    """Write a JSON dict to Redis. Returns True on success; fail-open → False."""
    r = _tg.get_redis_async()  # type: ignore[attr-defined]
    if r is None:
        return False
    try:
        await r.set(key, json.dumps(data, default=str), ex=ttl)
        return True
    except Exception:  # fail-open by contract
        logger.debug("tg_news:redis_set_failed key=%s", key, exc_info=True)
        return False


def _article_to_feed_item(article: NewsArticle) -> dict[str, Any]:
    """Shape a TG article for the website full-feed cache (with attribution).

    ``url`` prefers the canonical source URL so the website links to the
    original story; ``image_url`` carries an inline image link when the
    message text includes one (the website's ``NewsArticle.image_url``
    field). Both fall back gracefully to the channel post URL / empty.
    """
    return {
        "id": _news_id_for(article),
        "title": article.title,
        "url": extract_canonical_url(article) or article.source_url,
        "description": article.body[:300],
        "source": article.source_channel,
        "published_at": (article.posted_at or datetime.now(UTC)).isoformat(),
        "category": article.category or "news",
        "sentiment": "neutral",
        "kind": _TG_ARTICLE_KIND,
        "tier": "telegram",
        "image_url": extract_image_url(article) or getattr(article, "media", None) or "",
    }


async def inject_into_news_cache(articles: list[NewsArticle]) -> int:
    """Merge freshly-ingested TG articles into the website full-feed cache.

    The main feed (``/api/v1/news/feed`` + ``/headlines``) is served from the
    Redis cache populated by ``app.databus.news_provider`` (RSS + API only),
    so TG items would otherwise be invisible there — this prepends them with
    ``kind="telegram"`` + channel attribution. The Postgres-backed
    ``/api/v1/news/trending`` + ``POST /api/v1/news`` routes serve TG items
    directly from ``news_items`` regardless.

    Additive + fail-open: Redis down → returns 0; the aggregator's next
    periodic refresh replaces the cache wholesale and the injection re-applies
    on the next sweep. Returns the number of articles injected.
    """
    if not articles:
        return 0
    feed = await _redis_json_get(_NEWS_FEED_CACHE_KEY)
    existing: list[dict[str, Any]] = []
    if feed is not None:
        headlines = feed.get("headlines")
        if isinstance(headlines, dict):
            existing = list(headlines.get("articles") or [])
    ids = {str(a.get("id") or "") for a in existing}
    added = 0
    for article in articles:
        item = _article_to_feed_item(article)
        if item["id"] in ids:
            continue
        ids.add(item["id"])
        existing.insert(0, item)
        added += 1
        if len(existing) > _NEWS_FEED_CACHE_MAX:
            existing = existing[:_NEWS_FEED_CACHE_MAX]
    if not added:
        return 0
    merged = dict(feed or {})
    merged["headlines"] = {
        "articles": existing,
        "total": len(existing),
        "source": "rmi_news_aggregator+tg",
        "free": True,
    }
    merged["generated_at"] = datetime.now(UTC).isoformat()
    if not await _redis_json_set(_NEWS_FEED_CACHE_KEY, merged, 300):
        logger.info("tg_news:feed_cache_inject_skipped_redis_down added=%s", added)
        return 0
    logger.info("tg_news:feed_cache_injected added=%s total=%s", added, len(existing))
    return added


# ── Lightweight market-awareness snapshot (additive, fail-open) ─────────────


#: Redis key the bot's chat context can pull for a lightweight "what's moving"
#: snapshot built directly from the sweep (headlines + trending tokens).
#: ``app.domains.telegram.rugmunchbot.daily_context`` already covers the chat
#: context via ``/api/v1/news/trending`` (which includes TG items); this key
#: is the sweep's own direct artifact, refreshed every run.
_MARKET_AWARENESS_KEY = "rmi:news:market_awareness"
_MARKET_AWARENESS_TTL = 3600
_MARKET_AWARENESS_HEADLINES = 5
_MARKET_AWARENESS_TOKENS = 8


async def publish_market_awareness(articles: list[NewsArticle], scored: list[Any]) -> bool:
    """Write a lightweight market-awareness snapshot to Redis.

    Snapshot shape::

        {
            "headlines": [{"title", "source", "url"}, ...],   # top by importance
            "trending_tokens": ["SOL", "PEPE", ...],          # most-mentioned $SYMBOLs
            "article_count": int,
            "generated_at": ISO-8601,
            "source": "tg_news_sweep",
        }

    Pure selection (``importance.top_headlines`` / ``trending_tokens``) over
    the already-scored sweep; the only I/O is one fail-open Redis write.
    Returns True when the snapshot was written.
    """
    if not articles:
        return False
    try:
        headlines = [
            {
                "title": article.title,
                "source": article.source_channel,
                "url": article.source_url,
            }
            for article in top_headlines(list(scored), top_n=_MARKET_AWARENESS_HEADLINES)
        ]
        tokens = trending_tokens(articles, top_n=_MARKET_AWARENESS_TOKENS)
        snapshot = {
            "headlines": headlines,
            "trending_tokens": tokens,
            "article_count": len(articles),
            "generated_at": datetime.now(UTC).isoformat(),
            "source": "tg_news_sweep",
        }
        if not await _redis_json_set(_MARKET_AWARENESS_KEY, snapshot, _MARKET_AWARENESS_TTL):
            logger.info("tg_news:market_awareness_skipped_redis_down articles=%s", len(articles))
            return False
        logger.info("tg_news:market_awareness_published articles=%s tokens=%s", len(articles), len(tokens))
        return True
    except Exception as exc:  # fail-open by contract
        logger.warning("tg_news:market_awareness_failed err=%s", str(exc)[:200])
        return False
