"""Telegram news-channel source config, categorization, and sweep.

Submodule of ``channels/telegram_news.py`` — owns the **fetch/registry** half
of the pipeline: the env-overridable channel list (explicit + default), the
followed-channel categorization (news / alpha / scam / other), the fail-open
headless-client bootstrap, the lookback time filter, and the bounded
``read → normalize → time-filter → dedupe`` sweep itself.

Public names are re-exported through ``channels/telegram_news.py`` (the shim);
module-level state that tests patch (``TG_NEWS_CHANNELS``,
``TG_NEWS_AUTO_DISCOVER``, ``TG_NEWS_MAX_CHANNELS``, ``_build_headless_client``)
is read through the shim at call time so ``patch.object(tg_news, ...)`` keeps
working — see the package docstring.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.domains.news_ingest.discovery import discover_followed_channels
from app.domains.news_ingest.ingest import (
    DEFAULT_DEDUP_COLLECTION,
    dedupe_articles,
    filter_seen_articles,
    read_channel_messages,
)
from app.domains.news_ingest.normalize import channel_message_to_article
from app.domains.telegram.rugmunchbot.support.logging import get_logger

if TYPE_CHECKING:
    from app.domains.news_ingest.models import ChannelMessage, NewsArticle
    from app.domains.telegram.rugmunchbot.channels import telegram_news as _tg
else:
    _tg = sys.modules[__package__ or ""]

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
TG_NEWS_MAX_CHANNELS = _env_int("RMI_TG_NEWS_MAX_CHANNELS", 40)
TG_NEWS_POST_LIMIT = _env_int("RMI_TG_NEWS_POST_LIMIT", 5)
TG_NEWS_PERSIST = _env_bool("RMI_TG_NEWS_PERSIST", True)
TG_NEWS_RAG = _env_bool("RMI_TG_NEWS_RAG", True)
TG_NEWS_AUTO_DISCOVER = _env_bool("RMI_TG_NEWS_AUTO_DISCOVER", True)
TG_NEWS_CHANNELS = _env_list("RMI_TG_NEWS_CHANNELS", list(_DEFAULT_CHANNELS))


# ── Followed-channel categorization (news / alpha / scam / other) ────────────


# Own channels (the account follows its own news/alerts/alpha channels) must
# never be auto-classified as news/alpha — they are already curated digests.
_CHANNEL_OWN_KEYWORDS: tuple[str, ...] = ("rugmunch", "rugmunchbot", "cryptorugmunch")

#: Alpha/whale channel title signals — routed to the alpha channel.
_CHANNEL_ALPHA_KEYWORDS: tuple[str, ...] = (
    "alpha",
    "whale",
    "whales",
    "signal",
    "signals",
    "calls",
    "call",
    "gem",
    "gems",
    "100x",
    "1000x",
    "moon",
    "mooner",
    "plays",
    "insider",
    "sniper",
    "smart money",
    "accumulation",
    "early entries",
    "pump group",
    "pnd",
)

#: News/media/market channel title signals — routed to the news channel.
_CHANNEL_NEWS_KEYWORDS: tuple[str, ...] = (
    "news",
    "cryptonews",
    "crypto",
    "coin",
    "coins",
    "finance",
    "market",
    "markets",
    "headlines",
    "daily",
    "today",
    "update",
    "updates",
    "media",
    "journal",
    "wire",
    "insight",
    "insights",
    "briefing",
    "report",
    "bulletin",
    "digest",
    "magazine",
    "times",
    "cointelegraph",
    "coindesk",
    "decrypt",
    "block",
    "bitcoin",
    "ethereum",
    "solana",
    "altcoin",
    "macro",
    "trading",
    "slate",
    "guru",
    "watcher",
)


def classify_channel_category(title: str, username: str) -> str:
    """Classify a followed channel by title/username heuristics.

    Returns one of ``news`` | ``alpha`` | ``scam`` | ``other``:

    * ``scam``  — reuses the existing scam/shill classification in
      ``channels/scam_watch.py`` (``_classify_dialog``) for **unambiguous**
      scam signals (pump/shill/airdrop/seed-phrase/rug/honeypot). Channels
      the scam sweep flags only as ``ALPHA_RUG_SHILL`` (alpha/calls/signals/
      gem/100x titles) are *not* excluded here — their call content routes to
      the alpha channel while scam_watch still scans their tokens
      independently. No keyword duplication.
    * ``alpha`` — whale/alpha/signal/calls-style titles → routed to the alpha
      channel.
    * ``news``  — news/media/market-style titles → routed to the news channel.
    * ``other`` — everything else (personal chats, groups, non-crypto) →
      excluded from the sweep entirely.

    Pure + fail-open (a broken scam_watch import never breaks categorization).
    """
    haystack = f"{title or ''} {username or ''}".lower()
    if any(word in haystack for word in _CHANNEL_OWN_KEYWORDS):
        return "other"
    try:
        from app.domains.telegram.rugmunchbot.channels.scam_watch import (
            ScamSourceType,
            _classify_dialog,
        )

        subtype = _classify_dialog(str(title or ""), str(username or ""))
        if subtype is not None and subtype != ScamSourceType.ALPHA_RUG_SHILL:
            return "scam"
    except Exception:  # fail-open by contract
        logger.debug("tg_news:scam_classify_unavailable", exc_info=True)
    if any(kw in haystack for kw in _CHANNEL_ALPHA_KEYWORDS):
        return "alpha"
    if any(kw in haystack for kw in _CHANNEL_NEWS_KEYWORDS):
        return "news"
    return "other"


# ── Source config ────────────────────────────────────────────────────────────


def channel_sources() -> list[dict[str, Any]]:
    """Source-config dicts (``username`` + ``category``) for the sweep.

    Returns the explicit ``RMI_TG_NEWS_CHANNELS`` list (falling back to
    ``_DEFAULT_CHANNELS``). This is the *fallback* list — when auto-discovery
    is enabled and the headless client is available,
    :func:`resolve_channel_sources` prefers the account's own channels.
    """
    return [{"username": u, "category": "crypto_news", "name": u} for u in _tg.TG_NEWS_CHANNELS]


async def resolve_channel_sources(client: Any | None = None) -> list[dict[str, Any]]:
    """Resolve the sweep's channel list, preferring the account's own channels.

    Priority (highest first):
      1. **Discovered** — the logged-in account's followed/member public
         channels, enumerated via Telethon ``get_dialogs()``
         (:func:`app.domains.news_ingest.discover_followed_channels`), gated
         by ``RMI_TG_NEWS_AUTO_DISCOVER`` (default on). Every discovered
         channel is categorized by :func:`classify_channel_category`:
         **news** → ``crypto_news`` and **alpha** → ``alpha`` are swept;
         **scam** channels are left to the scam_watch job (no duplication)
         and **other** channels (personal chats, non-crypto) are skipped.
         The result is bounded by ``RMI_TG_NEWS_MAX_CHANNELS``.
      2. **Explicit** — ``RMI_TG_NEWS_CHANNELS`` env list.
      3. **Default** — ``_DEFAULT_CHANNELS``.

    Fail-open: when auto-discovery is disabled, the client is missing, or
    discovery returns nothing, the explicit/default list is used.
    """
    if _tg.TG_NEWS_AUTO_DISCOVER and client is not None:
        try:
            discovered = await discover_followed_channels(client)
        except Exception as exc:  # discovery must never break the sweep
            logger.warning("tg_news:auto_discover_failed err=%s", str(exc)[:200])
            discovered = []
        if discovered:
            sources: list[dict[str, Any]] = []
            seen: set[str] = set()
            counts = {"news": 0, "alpha": 0, "scam": 0, "other": 0}
            for username, title in discovered:
                handle = str(username or "").strip().lstrip("@")
                if not handle or handle in seen:
                    continue
                seen.add(handle)
                category = classify_channel_category(str(title or ""), handle)
                counts[category] += 1
                if category in ("scam", "other"):
                    continue  # scam → scam_watch; other → not news-relevant
                category_tag = "crypto_news" if category == "news" else "alpha"
                sources.append(
                    {
                        "username": handle,
                        "category": category_tag,
                        "name": str(title or handle),
                        "channel_category": category,
                    }
                )
                if len(sources) >= _tg.TG_NEWS_MAX_CHANNELS:
                    break
            if sources:
                logger.info(
                    "tg_news:using_discovered_channels count=%s by_category=%s",
                    len(sources),
                    counts,
                )
                return sources
            logger.debug("tg_news:auto_discover_no_news_channels fallback_to_explicit counts=%s", counts)
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
    is on and the client exposes ``get_dialogs``) are primary — categorized
    into news / alpha / scam / other (scam left to scam_watch, other skipped)
    and bounded by ``RMI_TG_NEWS_MAX_CHANNELS`` — and the explicit
    ``RMI_TG_NEWS_CHANNELS`` / ``_DEFAULT_CHANNELS`` list is the fallback.

    Dedup is two-layered: in-batch (content hash / source URL) then
    cross-run via the shared Redis content-hash sets
    (:func:`app.domains.news_ingest.ingest.filter_seen_articles`) so content
    seen in a previous sweep — or ingested by the RAG pipeline — is never
    re-read/re-embedded (Redis down → fail-open, everything treated as fresh).
    """
    if client is None:
        client = await _tg._build_headless_client()  # type: ignore[attr-defined]
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
    articles = dedupe_articles(articles)
    articles, seen_skipped = filter_seen_articles(articles, DEFAULT_DEDUP_COLLECTION)
    if seen_skipped:
        logger.info("tg_news:cross_run_dedup skipped=%s kept=%s", seen_skipped, len(articles))
    return articles
