"""Telegram news-channel ingestion → website news + bot news channel.

Sweeps a curated set of public crypto-news Telegram channels (via the same
headless Telethon user-session client the alerts domain uses), normalizes the
recent messages with ``app.domains.news_ingest``, then:

1. **Persists** newly-seen articles into the website news table
   (``news_items``, the table the ``/api/v1/news/*`` routes serve) — mirroring
   the RSS ingest path in ``app.domains.news.ingest``.
2. **Posts** the important subset to the bot's news channel (same chat id as
   the existing ``post_news_update`` job; same ``format_news_post`` +
   ``_safe_send`` machinery). Importance is the reranker-derived score from
   ``channels/importance.py`` (Infinity ``BAAI/bge-reranker-v2-m3``); every
   article is persisted, only those above ``RMI_NEWS_IMPORTANCE_THRESHOLD``
   are posted (fail-open: heuristic fallback when Infinity is down).

Everything fails open: a missing headless client, a flaky channel, Postgres
being unreachable, or a Telegram send error logs and skips — the JobQueue keeps
running and the other half of the pipeline still works.

Read mechanism
--------------
``python-telegram-bot``'s Bot API cannot read arbitrary public channels the bot
is not a member of, so channel reads go through the **headless Telethon client**
(``app.domains.telegram.headless.get_headless_client`` → MTProto user session)
via the canonical thin reader ``app.domains.news_ingest.read_channel_messages``.
This is the exact mechanism the alerts domain (``telegram_transport``) and the
daily pipeline (``app.domains.content.daily_pipeline``) already use.

Setup required before the sweep returns data (documented, never hardcoded):
* ``telethon`` is a runtime dependency (see ``requirements.txt`` /
  ``pyproject.toml``).
* gopass entries the headless client reads in
  ``app.domains.telegram.headless_credentials``: ``rmi/telegram/api_id`` and
  ``rmi/telegram/api_hash`` exist in the store; ``rmi/telegram/phone`` must be
  created (``insert a phone entry in the secrets store (see headless_credentials)``). Until the phone entry exists
  the sweep degrades to ``[]`` and logs ``headless_client_unavailable``.

Channel list
------------
The sweep prefers the account's own followed/member public channels, discovered
at runtime via Telethon ``client.get_dialogs()``
(``app.domains.news_ingest.discover_followed_channels``) — gated by
``RMI_TG_NEWS_AUTO_DISCOVER`` (default on). When discovery is disabled,
returns nothing, or the client is unavailable, the sweep falls back to the
env-overridable explicit list ``RMI_TG_NEWS_CHANNELS`` (comma- or
pipe-separated @handles), then to ``_DEFAULT_CHANNELS`` (10 well-known public
crypto-news channels). Several default handles carry ``[REVIEW]`` notes in
``app/databus/tg_channels.json``; verify them before production monitoring.
No secrets involved.

Dedup
-----
Three layers:
* **In-batch** — ``dedupe_articles`` (content-hash / source-URL first-wins) so
  the same story forwarded across channels collapses.
* **Cross-run** — the ``news_items.news_id`` primary key is derived from the
  canonical ``https://t.me/<channel>/<message_id>`` URL, so re-sweeping the
  same message is skipped by the ``SELECT 1 ... WHERE news_id=$1`` guard.
* **Post-level** — the scheduler's in-memory ``_remember`` set (key prefix
  ``tgnews``) stops the digest re-posting the same article on the next tick.

Schedule
--------
Registered additively in ``channels/scheduler.py`` (``register_channel_jobs``):
every ``RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES`` (default 30 — ~2 posts/hour on
the news channel) minutes, gated by ``RMI_TG_NEWS_CHANNEL_ENABLED`` (default
on) and a resolvable news chat id.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.domains.news_ingest.discovery import discover_followed_channels
from app.domains.news_ingest.ingest import dedupe_articles, read_channel_messages
from app.domains.news_ingest.normalize import channel_message_to_article
from app.domains.telegram.rugmunchbot.channels.formatters import format_news_post
from app.domains.telegram.rugmunchbot.channels.importance import (
    IMPORTANCE_THRESHOLD,
    score_importance,
    select_important,
)
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

    from app.domains.news_ingest.models import ChannelMessage, NewsArticle

logger = get_logger("rmi_bot.channels.tg_news")

# ── Channel list (env-overridable, no secrets) ──────────────────────────────

#: Default public crypto-news channels. All real, public handles; the ones also
#: listed in ``app/databus/tg_channels.json`` carry that registry's confidence
#: notes (some are [REVIEW] — re-verify before production monitoring).
_DEFAULT_CHANNELS: tuple[str, ...] = (
    "cointelegraph",  # official CoinTelegraph
    "wublockchain",  # Wu Blockchain (Colin Wu) — high-frequency news aggregator
    "coindesk",  # official CoinDesk
    "TheBlock__",  # The Block [REVIEW: exact handle, double underscore]
    "cryptoslate",  # CryptoSlate
    "beincrypto",  # BeInCrypto
    "cryptonews",  # Cryptonews.com [REVIEW: verify handle]
    "PANewsOfficial",  # PANews [REVIEW: verify casing]
    "WatcherGuru",  # Watcher Guru
    "bitcoinmagazine",  # Bitcoin Magazine [REVIEW: verify handle]
)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (TypeError, ValueError):
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return list(default)
    parts = [p.strip().lstrip("@") for p in re_split(raw)]
    return [p for p in parts if p]


def re_split(value: str) -> list[str]:
    """Split an env list on commas, pipes, or whitespace."""
    return [p for p in value.replace(",", "|").replace(" ", "|").split("|") if p]


TG_NEWS_ENABLED = _env_bool("RMI_TG_NEWS_CHANNEL_ENABLED", True)
TG_NEWS_INTERVAL_MINUTES = _env_int("RMI_CHANNEL_TG_NEWS_INTERVAL_MINUTES", 30)
TG_NEWS_SWEEP_HOURS = _env_int("RMI_TG_NEWS_SWEEP_HOURS", 6)
TG_NEWS_PER_CHANNEL_LIMIT = _env_int("RMI_TG_NEWS_PER_CHANNEL_LIMIT", 30)
TG_NEWS_POST_LIMIT = _env_int("RMI_TG_NEWS_POST_LIMIT", 5)
TG_NEWS_PERSIST = _env_bool("RMI_TG_NEWS_PERSIST", True)
TG_NEWS_AUTO_DISCOVER = _env_bool("RMI_TG_NEWS_AUTO_DISCOVER", True)
TG_NEWS_CHANNELS = _env_list("RMI_TG_NEWS_CHANNELS", list(_DEFAULT_CHANNELS))


# ── Source config ────────────────────────────────────────────────────────────


def channel_sources() -> list[dict[str, Any]]:
    """Source-config dicts (``username`` + ``category``) for the sweep.

    Returns the explicit ``RMI_TG_NEWS_CHANNELS`` list (falling back to
    ``_DEFAULT_CHANNELS``). This is the *fallback* list — when auto-discovery
    is enabled and the headless client is available,
    :func:`resolve_channel_sources` prefers the account's own channels.
    """
    return [{"username": u, "category": "crypto_news", "name": u} for u in TG_NEWS_CHANNELS]


async def resolve_channel_sources(client: Any | None = None) -> list[dict[str, Any]]:
    """Resolve the sweep's channel list, preferring the account's own channels.

    Priority (highest first):
      1. **Discovered** — the logged-in account's followed/member public
         channels, enumerated via Telethon ``get_dialogs()``
         (:func:`app.domains.news_ingest.discover_followed_channels`), gated
         by ``RMI_TG_NEWS_AUTO_DISCOVER`` (default on).
      2. **Explicit** — ``RMI_TG_NEWS_CHANNELS`` env list.
      3. **Default** — ``_DEFAULT_CHANNELS``.

    Fail-open: when auto-discovery is disabled, the client is missing, or
    discovery returns nothing, the explicit/default list is used.
    """
    if TG_NEWS_AUTO_DISCOVER and client is not None:
        try:
            discovered = await discover_followed_channels(client)
        except Exception as exc:  # discovery must never break the sweep
            logger.warning("tg_news:auto_discover_failed err=%s", str(exc)[:200])
            discovered = []
        if discovered:
            logger.info("tg_news:using_discovered_channels count=%s", len(discovered))
            return [{"username": username, "category": "crypto_news", "name": title} for username, title in discovered]
        logger.debug("tg_news:auto_discover_empty_fallback_to_explicit")
    return channel_sources()


# ── Client bootstrap (fail-open) ─────────────────────────────────────────────


async def _build_headless_client() -> Any:
    """Return the singleton headless Telethon client, or ``None`` (never raises).

    When telethon is missing or the gopass API credentials are unpopulated the
    existing client raises; we log the exact setup needed and degrade.
    """
    try:
        from app.domains.telegram.headless import get_headless_client

        client = await get_headless_client()
        if getattr(client, "is_connected", False):
            return client
        return None
    except Exception as exc:  # fail-open by contract
        logger.warning("tg_news:headless_client_unavailable err=%s", str(exc)[:200])
        return None


# ── Time filter ──────────────────────────────────────────────────────────────


def _as_utc(value: datetime) -> datetime:
    """Normalize a (possibly naive) datetime to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _inside_window(message: ChannelMessage, cutoff: datetime) -> bool:
    """True when a message falls inside the lookback window.

    Messages without a date are kept (best-effort — cross-run dedup on the
    stable ``news_id`` still prevents re-ingesting ancient items).
    """
    posted = message.posted_at
    if posted is None:
        return True
    return _as_utc(posted) >= cutoff


# ── Sweep: read → normalize → time-filter → dedupe ──────────────────────────


async def sweep_telegram_news(
    *,
    client: Any | None = None,
    lookback_hours: int | None = None,
    per_channel_limit: int | None = None,
) -> list[NewsArticle]:
    """Read recent messages from the configured channels.

    Returns deduped ``NewsArticle`` objects newer than ``lookback_hours``
    (default ``TG_NEWS_SWEEP_HOURS``). Accepts an injected client (any object
    exposing ``iter_messages`` or the news_ingest ``read_messages`` protocol —
    used by tests); when omitted the headless singleton is built (fail-open →
    ``[]``).

    The channel list is resolved by :func:`resolve_channel_sources`: the
    account's own followed/member channels (when ``RMI_TG_NEWS_AUTO_DISCOVER``
    is on and the client exposes ``get_dialogs``) are primary; the explicit
    ``RMI_TG_NEWS_CHANNELS`` / ``_DEFAULT_CHANNELS`` list is the fallback.
    """
    if client is None:
        client = await _build_headless_client()
    if client is None:
        logger.warning("tg_news:sweep_skipped_no_client")
        return []

    sources = await resolve_channel_sources(client)
    if not sources:
        logger.info("tg_news:no_sources_configured")
        return []

    lookback = max(1, lookback_hours or TG_NEWS_SWEEP_HOURS)
    per_channel = max(5, per_channel_limit or TG_NEWS_PER_CHANNEL_LIMIT)
    cutoff = datetime.now(UTC) - timedelta(hours=lookback)

    articles: list[NewsArticle] = []
    for source in sources:
        username = str(source.get("username") or "").strip().lstrip("@")
        if not username:
            continue
        category = str(source.get("category") or "crypto_news")
        messages = await read_channel_messages(client, username, per_channel)
        for message in messages:
            if not _inside_window(message, cutoff):
                continue
            article = channel_message_to_article(message, category)
            if article is not None:
                articles.append(article)
    return dedupe_articles(articles)


# ── Persist to the website news table (news_items) ───────────────────────────


def _news_id_for(article: NewsArticle) -> str:
    """Stable news_id from the canonical t.me URL (message id = dedup key)."""
    digest = hashlib.sha256(article.source_url.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"tg:{digest}"


# Chain keyword map mirrors ``app.domains.news.ingest._detect_chains`` so TG
# articles carry the same chain tags as RSS articles on the website.
_CHAIN_KEYWORDS: dict[str, tuple[str, ...]] = {
    "solana": ("solana", "sol ", "$sol"),
    "ethereum": ("ethereum", "eth ", "$eth", "ether"),
    "bitcoin": ("bitcoin", "btc ", "$btc"),
    "base": ("base ", "basechain", "coinbase base"),
    "arbitrum": ("arbitrum", "arb "),
    "polygon": ("polygon", "matic"),
    "bsc": ("bsc", "bnb chain", "binance smart chain"),
    "tron": ("tron", "trx "),
    "avalanche": ("avalanche", "avax"),
}


def _detect_chains(text: str) -> list[str]:
    """Ordered chain mentions found in article text."""
    lowered = (text or "").lower()
    return [chain for chain, keywords in _CHAIN_KEYWORDS.items() if any(k in lowered for k in keywords)]


def _tokens_from_entities(entities: list[str]) -> list[str]:
    """Map ``$TICKER`` entities to the bare ticker list the site expects."""
    return [entity[1:] for entity in entities if entity.startswith("$")]


def _article_to_news_item(article: NewsArticle) -> Any | None:
    """Map a ``NewsArticle`` to the catalog ``NewsItem`` row shape (fail-open).

    Reuses ``app.domains.news.ratings.rate_article`` (deterministic heuristic —
    the same rating card the RSS ingest writes) so TG articles get the same
    bias/sentiment/factual/tier columns on the website.
    """
    try:
        from pydantic import HttpUrl

        from app.catalog.models import NewsItem, utcnow
        from app.domains.news.ratings import rate_article

        rating = rate_article(title=article.title, content=article.body, source=article.source_channel)
        published = article.posted_at if article.posted_at is not None else utcnow()
        return NewsItem(
            news_id=_news_id_for(article),
            url=HttpUrl(article.source_url),
            title=article.title,
            summary=article.body,
            body_markdown=article.body,
            source=article.source_channel,
            published_at=published,
            ingested_at=utcnow(),
            chains_mentioned=_detect_chains(article.body),
            tokens_mentioned=_tokens_from_entities(article.entities),
            sentiment_score=rating.sentiment_score / 100.0,  # -1..1 column scale
            bias=rating.bias,
            sentiment_label=rating.sentiment_label,
            factual_rating=rating.factual_rating,
            source_tier=rating.source_tier,
            rating_confidence=rating.confidence,
        )
    except Exception as exc:  # fail-open: a malformed article never aborts the batch
        logger.warning("tg_news:article_build_failed title=%s err=%s", article.title[:60], str(exc)[:160])
        return None


async def persist_articles(articles: list[NewsArticle]) -> dict[str, int]:
    """Insert new articles into ``news_items`` (website feed), deduped by PK.

    Fail-open: Postgres outage or per-item errors log and are counted; the
    caller still posts the Telegram digest. Returns
    ``{"ingested", "duplicate", "errors"}``.
    """
    counts = {"ingested": 0, "duplicate": 0, "errors": 0}
    if not TG_NEWS_PERSIST:
        logger.info("tg_news:persist_disabled")
        return counts
    try:
        from app.catalog.service import get_catalog

        catalog = get_catalog()
        await catalog._init_stores()  # catalog store probe (same as RSS ingest)
        if not catalog._health.postgres:
            logger.warning("tg_news:persist_skipped_postgres_unavailable")
            return counts

        items: list[Any] = []
        for article in articles:
            item = _article_to_news_item(article)
            if item is None:
                counts["errors"] += 1
            else:
                items.append(item)
        if not items:
            return counts

        async with catalog._pg_pool.acquire() as conn:
            for item in items:
                try:
                    existing = await conn.fetchval("SELECT 1 FROM news_items WHERE news_id=$1", item.news_id)
                    if existing:
                        counts["duplicate"] += 1
                        continue
                    await conn.execute(
                        """
                        INSERT INTO news_items (
                            news_id, url, title, summary, body_markdown,
                            source, published_at, ingested_at,
                            chains_mentioned, tokens_mentioned, sentiment_score,
                            bias, sentiment_label, factual_rating, source_tier,
                            rating_confidence
                        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
                        """,
                        item.news_id,
                        str(item.url),
                        item.title,
                        item.summary,
                        item.body_markdown,
                        item.source,
                        item.published_at,
                        item.ingested_at,
                        item.chains_mentioned,
                        item.tokens_mentioned,
                        item.sentiment_score,
                        item.bias,
                        item.sentiment_label,
                        item.factual_rating,
                        item.source_tier,
                        item.rating_confidence,
                    )
                    counts["ingested"] += 1
                except Exception:  # fail-open per item
                    counts["errors"] += 1
                    logger.debug("tg_news:item_insert_failed news_id=%s", item.news_id, exc_info=True)
    except Exception as exc:  # fail-open: an outage never crashes the job
        logger.warning("tg_news:persist_failed err=%s", str(exc)[:200])
        counts["errors"] = len(articles)
    return counts


# ── Post to the bot's news channel ───────────────────────────────────────────


def _article_to_post_dict(article: NewsArticle) -> dict[str, Any]:
    """Shape a ``NewsArticle`` for ``format_news_post``."""
    return {
        "title": article.title,
        "summary": article.body,
        "url": article.source_url,
        "source": article.source_channel,
        "sentiment_label": "",
        "factual_rating": "",
    }


async def post_tg_news_update(ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Sweep TG news channels → persist to website → post important digest.

    Returns 1 when a digest was posted, 0 otherwise. Fails open at every step:
    missing chat id, no client, empty sweep, Postgres outage, or a Telegram
    send error all log and return without raising.

    Importance gate: every swept article is persisted (store everything), but
    only the articles ``select_important`` keeps (rerank importance >=
    ``RMI_NEWS_IMPORTANCE_THRESHOLD``, or the top-N under the heuristic
    fallback when Infinity /rerank is down) are posted to the channel.
    """
    # Lazy import keeps the scheduler ↔ this module import graph acyclic
    # (scheduler.py imports this module at module scope for registration).
    from app.domains.telegram.rugmunchbot.channels.scheduler import (
        _chat_id,
        _dedupe_key,
        _remember,
        _safe_send,
    )

    chat_id = _chat_id("news")
    if not chat_id:
        return 0

    articles = await sweep_telegram_news()
    if not articles:
        logger.debug("tg_news:no_fresh_articles")
        return 0

    counts = await persist_articles(articles)
    logger.info(
        "tg_news:sweep_done articles=%s ingested=%s duplicate=%s errors=%s",
        len(articles),
        counts["ingested"],
        counts["duplicate"],
        counts["errors"],
    )

    scored = await score_importance(articles)
    selected = select_important(scored, top_n=TG_NEWS_POST_LIMIT)
    if not selected:
        logger.info(
            "tg_news:importance_gate_none_passed articles=%s threshold=%s fallback=%s",
            len(articles),
            IMPORTANCE_THRESHOLD,
            any(s.fallback for s in scored),
        )
        return 0
    logger.info(
        "tg_news:importance_selected selected=%s of %s threshold=%s fallback=%s",
        len(selected),
        len(scored),
        IMPORTANCE_THRESHOLD,
        any(s.fallback for s in scored),
    )

    fresh = [
        scored_article.article
        for scored_article in selected
        if _remember("tgnews", _dedupe_key("tgnews", _article_to_post_dict(scored_article.article)))
    ]
    if not fresh:
        logger.debug("tg_news:all_already_posted")
        return 0

    ok = await _safe_send(
        ctx.bot,
        chat_id,
        format_news_post([_article_to_post_dict(a) for a in fresh[:TG_NEWS_POST_LIMIT]]),
    )
    if ok:
        logger.info("tg_news:posted count=%s", min(len(fresh), TG_NEWS_POST_LIMIT))
    return 1 if ok else 0


__all__ = [
    "TG_NEWS_AUTO_DISCOVER",
    "TG_NEWS_CHANNELS",
    "TG_NEWS_ENABLED",
    "TG_NEWS_INTERVAL_MINUTES",
    "TG_NEWS_PER_CHANNEL_LIMIT",
    "TG_NEWS_POST_LIMIT",
    "TG_NEWS_SWEEP_HOURS",
    "channel_sources",
    "persist_articles",
    "post_tg_news_update",
    "resolve_channel_sources",
    "sweep_telegram_news",
]
