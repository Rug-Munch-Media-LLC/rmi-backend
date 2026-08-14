"""Channel-monitoring ingestion: read → normalize → dedupe.

``read_channel_messages`` is a thin async reader over the headless Telegram
client. It never edits ``app.domains.telegram.headless_client`` and never
raises — any failure degrades to an empty list so a single flaky channel can
not take down the whole sweep.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.domains.news_ingest.models import ChannelMessage, NewsArticle
from app.domains.news_ingest.normalize import channel_message_to_article
from app.rag.dedup import content_hash, url_hash

logger = get_logger(__name__)

# Injectable reader protocol: async (channel_username, limit) -> messages.
ReadMessagesCallable = Callable[[str, int], Awaitable[list[ChannelMessage]]]

#: Redis dedup-set namespace for TG-sourced articles. Reuses the shared
#: ``app.rag.dedup`` ZSETs (``rag:dedup:content:<collection>``) so the news
#: sweep and the RAG pipeline never re-embed content the other already saw.
DEFAULT_DEDUP_COLLECTION: str = "tg_news"


# ── Dedup (pure, no Redis) ────────────────────────────────────────────────


def dedupe_articles(articles: list[NewsArticle]) -> list[NewsArticle]:
    """First-wins dedup on content hash, falling back to source URL.

    Uses the pure ``app.rag.dedup`` helpers (``content_hash`` / ``url_hash``)
    so batch dedup needs no Redis. Preserves input order; articles whose
    hash matches an earlier one are dropped.
    """
    seen: set[str] = set()
    result: list[NewsArticle] = []
    for article in articles:
        if article.content_hash:
            key = article.content_hash.strip()
        elif article.source_url:
            key = url_hash(article.source_url)
        else:
            key = content_hash(f"{article.title}\n{article.body}")
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(article)
    return result


def _article_fingerprint(article: NewsArticle) -> str:
    """Stable content fingerprint for the shared Redis dedup sets."""
    if article is None:
        return ""
    return article.content_hash or content_hash(f"{article.title}\n{article.body}")


def filter_seen_articles(
    articles: list[NewsArticle], collection: str = DEFAULT_DEDUP_COLLECTION
) -> tuple[list[NewsArticle], int]:
    """Drop articles already recorded in the shared Redis content-hash sets.

    Check-only (never marks): callers mark with :func:`mark_articles_ingested`
    only after the article was actually persisted, so a transient Postgres
    outage never permanently hides an un-persisted story.

    Reuses ``app.rag.dedup.is_duplicate_content`` — the same Redis ZSET the
    RAG pipeline maintains — under a dedicated *collection* namespace
    (``rag:dedup:content:tg_news``). Fail-open: Redis down / auth-failure
    treats every article as fresh, matching the rest of the ingest layer.

    Returns ``(fresh, skipped)``; ``fresh`` preserves input order.
    """
    from app.rag.dedup import is_duplicate_content

    fresh: list[NewsArticle] = []
    skipped = 0
    for article in articles or []:
        fp = _article_fingerprint(article)
        if not fp:
            fresh.append(article)
            continue
        try:  # fail-open by contract: dedup must never raise or drop articles
            duplicate = is_duplicate_content(fp, collection)
        except Exception:
            logger.debug("news_ingest:dedup_check_failed", exc_info=True)
            duplicate = False
        if duplicate:
            skipped += 1
            continue
        fresh.append(article)
    if skipped:
        logger.info("news_ingest:seen_skipped collection=%s fresh=%s skipped=%s", collection, len(fresh), skipped)
    return fresh, skipped


def mark_articles_ingested(articles: list[NewsArticle], collection: str = DEFAULT_DEDUP_COLLECTION) -> int:
    """Record article content hashes in the shared Redis dedup sets.

    Best-effort (never raises): the ``app.rag.dedup`` markers are fail-open,
    so a Redis outage only means the next sweep re-reads the same content —
    the Postgres ``news_id`` guard still blocks duplicate persistence.

    Returns the number of articles marked.
    """
    from app.rag.dedup import mark_content_ingested

    marked = 0
    for article in articles or []:
        fp = _article_fingerprint(article)
        if not fp:
            continue
        mark_content_ingested(fp, collection)
        marked += 1
    return marked


# ── Telethon message conversion ────────────────────────────────────────────


def _message_to_channel_message(msg: Any, channel_username: str) -> ChannelMessage | None:
    """Convert a Telethon-style message object (duck-typed) to a ChannelMessage."""
    try:
        message_id = getattr(msg, "id", None)
        if message_id is None:
            return None
        text = getattr(msg, "message", None) or getattr(msg, "text", None) or ""
        text = text if isinstance(text, str) else str(text)

        chat = getattr(msg, "chat", None)
        channel = ""
        if chat is not None:
            channel = getattr(chat, "title", None) or getattr(chat, "username", None) or ""

        forwarded_from: str | None = None
        fwd = getattr(msg, "fwd_from", None)
        if fwd is not None:
            forwarded_from = getattr(fwd, "from_name", None)
            if forwarded_from is None:
                peer = getattr(fwd, "from_id", None)
                forwarded_from = str(peer) if peer is not None else "forwarded"

        views = getattr(msg, "views", None)
        return ChannelMessage(
            channel=channel or channel_username,
            channel_username=channel_username,
            message_id=int(message_id),
            text=text,
            media_type=_media_type(getattr(msg, "media", None)),
            posted_at=getattr(msg, "date", None),
            forwarded_from=forwarded_from,
            views=int(views) if views is not None else None,
            reactions=_reaction_counts(getattr(msg, "reactions", None)),
        )
    except Exception:
        logger.debug("news_ingest_convert_failed", exc_info=True)
        return None


def _media_type(media: Any) -> str:
    if media is None:
        return "text"
    name = type(media).__name__.lower()
    for kind in ("photo", "video", "document", "audio", "sticker", "gif", "poll", "voice"):
        if kind in name:
            return kind
    return "media"


def _reaction_counts(reactions: Any) -> dict[str, int] | None:
    results = getattr(reactions, "results", None)
    if not results:
        return None
    counts: dict[str, int] = {}
    for item in results:
        reaction = getattr(item, "reaction", None)
        count = int(getattr(item, "count", None) or 0)
        key: Any = None
        if reaction is not None:
            key = getattr(reaction, "emoticon", None) or getattr(reaction, "document_id", None) or str(reaction)
        if key is not None:
            counts[str(key)] = count
    return counts or None


# ── Thin reader ────────────────────────────────────────────────────────────


async def read_channel_messages(client: Any, channel_username: str, limit: int = 50) -> list[ChannelMessage]:
    """Read the last *limit* messages from a channel. Never raises.

    Accepts either:
      * the ``HeadlessTelegramClient`` wrapper (its underlying Telethon
        ``_client.iter_messages`` is used), or
      * any object exposing ``iter_messages(entity, limit=...)`` (Telethon
        style), or
      * an injectable fake exposing ``read_messages(channel_username, limit)``
        returning ``list[ChannelMessage]`` (used by tests).

    Any failure → ``[]`` (fail-open, logged at warning).
    """
    try:
        if client is None or not channel_username:
            return []
        raw = getattr(client, "_client", None) or client

        if callable(getattr(raw, "iter_messages", None)):
            messages: list[ChannelMessage] = []
            async for msg in raw.iter_messages(channel_username, limit=max(1, int(limit))):
                converted = _message_to_channel_message(msg, channel_username)
                if converted is not None:
                    messages.append(converted)
            return messages

        read_messages = getattr(client, "read_messages", None)
        if callable(read_messages):
            result = await read_messages(channel_username, limit)
            if not result:
                return []
            return [m for m in result if isinstance(m, ChannelMessage)]

        return []
    except Exception as e:
        logger.warning("news_ingest_read_failed channel=%s err=%s", channel_username, e)
        return []


# ── Orchestration ──────────────────────────────────────────────────────────


def channel_username_from_config(channel: dict[str, Any]) -> str:
    """Resolve the channel @username from a source-config dict (never raises)."""
    for key in ("username", "channel_username", "id"):
        value = channel.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lstrip("@")
    url = str(channel.get("url") or "")
    if "t.me/" in url:
        return url.split("t.me/", 1)[1].split("/", 1)[0].strip()
    return ""


async def sweep_channels(client: Any, channels: list[dict[str, Any]], limit: int = 50) -> list[NewsArticle]:
    """Read → normalize → dedupe across every configured channel.

    Each entry of *channels* follows the ``unified_news_sources.json`` schema
    (``username``/``id``/``url`` + optional ``category``). Channels without a
    resolvable username are skipped; individual channel failures degrade to
    empty without aborting the sweep.
    """
    articles: list[NewsArticle] = []
    for channel in channels or []:
        username = channel_username_from_config(channel)
        if not username:
            continue
        category = str(channel.get("category") or "crypto_news")
        messages = await read_channel_messages(client, username, limit)
        for message in messages:
            article = channel_message_to_article(message, category)
            if article is not None:
                articles.append(article)
    return dedupe_articles(articles)
