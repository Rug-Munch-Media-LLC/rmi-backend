"""Unit tests for the Telegram news-channel ingestion (channels/telegram_news.py).

Covers:
  * channel source config parsing (env list, defaults)
  * sweep: read → normalize → time-window filter → dedupe (fake reader)
  * news_id stability + chain/token extraction
  * Postgres persistence (fail-open paths)
  * the scheduled job (post + dedupe, skip when channel unset)

No live network I/O — the reader is a fake, the headless client is patched,
and the catalog is mocked.
"""

from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domains.news_ingest.models import ChannelMessage, NewsArticle
from app.domains.telegram.rugmunchbot.channels import (
    scheduler as channels_scheduler,
    telegram_news as tg_news,
)
from app.domains.telegram.rugmunchbot.channels.importance import ScoredArticle

NOW = datetime.now(UTC)


def _msg(
    text: str,
    *,
    username: str = "cointelegraph",
    message_id: int = 1,
    posted_at: datetime | None = None,
    forwarded: str | None = None,
) -> ChannelMessage:
    return ChannelMessage(
        channel=username.title(),
        channel_username=username,
        message_id=message_id,
        text=text,
        forwarded_from=forwarded,
        posted_at=posted_at or NOW,
    )


def _article(
    title: str,
    *,
    channel: str = "CoinTelegraph",
    username: str = "cointelegraph",
    message_id: int = 1,
    body: str | None = None,
) -> NewsArticle:
    return NewsArticle(
        title=title,
        body=body or title,
        source_channel=channel,
        source_url=f"https://t.me/{username}/{message_id}",
        category="crypto_news",
        posted_at=NOW,
        content_hash="x",
        entities=["$BTC"],
    )


class FakeReader:
    """news_ingest-style fake: ``read_messages(username, limit)``."""

    def __init__(self, messages_by_channel: dict[str, list[ChannelMessage]]) -> None:
        self.messages_by_channel = messages_by_channel

    async def read_messages(self, channel_username: str, limit: int) -> list[ChannelMessage]:
        return self.messages_by_channel.get(channel_username, [])[:limit]


@pytest.fixture(autouse=True)
def _reset_dedupe() -> None:
    channels_scheduler._posted_keys.clear()
    yield
    channels_scheduler._posted_keys.clear()


# ── Config / sources ─────────────────────────────────────────────────────────


class TestSources:
    def test_defaults_have_curated_handles(self) -> None:
        assert len(tg_news._DEFAULT_CHANNELS) == 33
        assert "cointelegraph" in tg_news._DEFAULT_CHANNELS
        assert "WatcherGuru" in tg_news._DEFAULT_CHANNELS
        # Curated set spans news / security / on-chain — spot-check each tier.
        assert "coindesk" in tg_news._DEFAULT_CHANNELS
        assert "CertiKAlert" in tg_news._DEFAULT_CHANNELS
        assert "lookonchain" in tg_news._DEFAULT_CHANNELS
        # Dead/renamed handles that logged "No user has X as username" are
        # excluded from the default list (auto-disable covers discovered ones).
        for dead in ("wublockchain", "TheBlock__", "beincrypto", "cryptonews"):
            assert dead not in tg_news._DEFAULT_CHANNELS

    def test_env_override_parses_commas_and_pipes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RMI_TG_NEWS_CHANNELS", "cointelegraph|wublockchain,WatcherGuru")
        assert tg_news.re_split("cointelegraph|wublockchain,WatcherGuru") == [
            "cointelegraph",
            "wublockchain",
            "WatcherGuru",
        ]
        monkeypatch.setattr(
            tg_news,
            "TG_NEWS_CHANNELS",
            ["cointelegraph", "wublockchain", "WatcherGuru"],
        )
        assert tg_news.channel_sources() == [
            {"username": "cointelegraph", "category": "crypto_news", "name": "cointelegraph"},
            {"username": "wublockchain", "category": "crypto_news", "name": "wublockchain"},
            {"username": "WatcherGuru", "category": "crypto_news", "name": "WatcherGuru"},
        ]

    def test_env_override_strips_at_signs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_CHANNELS", ["cointelegraph", "wublockchain"])
        monkeypatch.setenv("RMI_TG_NEWS_CHANNELS", "@cointelegraph, @wublockchain")
        # re-evaluate the env list (module-level default was computed at import)
        tg_news.TG_NEWS_CHANNELS = tg_news._env_list("RMI_TG_NEWS_CHANNELS", list(tg_news._DEFAULT_CHANNELS))
        assert tg_news.TG_NEWS_CHANNELS == ["cointelegraph", "wublockchain"]


# ── Sweep ────────────────────────────────────────────────────────────────────


class TestSweep:
    async def test_filters_outside_lookback_window(self) -> None:
        old = NOW - timedelta(hours=48)
        reader = FakeReader(
            {
                "cointelegraph": [
                    _msg("Bitcoin just smashed its all-time high as ETF inflows hit a record", message_id=2),
                    _msg("Ethereum devs debate the next hardfork upgrade timeline", message_id=1, posted_at=old),
                ]
            }
        )
        articles = await tg_news.sweep_telegram_news(client=reader, lookback_hours=6, per_channel_limit=10)
        assert [a.title for a in articles] == ["Bitcoin just smashed its all-time high as ETF inflows hit a record"]

    async def test_dedupes_same_content_across_channels(self) -> None:
        body = "Bitcoin ETF inflows hit a record high today as institutions pile in"
        reader = FakeReader(
            {
                "cointelegraph": [_msg(body, message_id=1)],
                "wublockchain": [_msg(body, message_id=2, username="wublockchain")],
            }
        )
        articles = await tg_news.sweep_telegram_news(client=reader, lookback_hours=6, per_channel_limit=10)
        assert len(articles) == 1

    async def test_drops_junk_and_promo(self) -> None:
        reader = FakeReader(
            {
                "cointelegraph": [
                    _msg("JOIN OUR CHANNEL NOW 🚀🚀", message_id=1),
                    _msg("Bitcoin crosses $120k as ETF inflows accelerate with record demand", message_id=2),
                ]
            }
        )
        articles = await tg_news.sweep_telegram_news(client=reader, lookback_hours=6, per_channel_limit=10)
        assert [a.title for a in articles] == ["Bitcoin crosses $120k as ETF inflows accelerate with record demand"]

    async def test_returns_empty_when_no_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "_build_headless_client", AsyncMock(return_value=None))
        assert await tg_news.sweep_telegram_news() == []

    async def test_never_raises_on_flaky_reader(self) -> None:
        class BrokenReader:
            async def read_messages(self, channel_username: str, limit: int) -> list[ChannelMessage]:
                raise RuntimeError("boom")

        assert await tg_news.sweep_telegram_news(client=BrokenReader(), lookback_hours=6) == []


# ── News id + entity helpers ─────────────────────────────────────────────────


class TestDerivedFields:
    def test_news_id_is_stable_and_prefixed(self) -> None:
        article = _article("Headline", username="cointelegraph", message_id=42)
        assert tg_news._news_id_for(article) == tg_news._news_id_for(article)
        assert tg_news._news_id_for(article).startswith("tg:")
        other = _article("Headline", username="cointelegraph", message_id=43)
        assert tg_news._news_id_for(article) != tg_news._news_id_for(other)

    def test_detect_chains(self) -> None:
        assert tg_news._detect_chains("SOL pumps while Bitcoin dumps") == ["solana", "bitcoin"]
        assert tg_news._detect_chains("nothing crypto here") == []

    def test_tokens_from_entities(self) -> None:
        assert tg_news._tokens_from_entities(["$BTC", "$ETH", "0xabc"]) == ["BTC", "ETH"]

    def test_article_to_post_dict_shape(self) -> None:
        d = tg_news._article_to_post_dict(_article("H", channel="CoinTelegraph"))
        assert d["title"] == "H"
        assert d["url"].startswith("https://t.me/")
        assert d["source"] == "CoinTelegraph"


# ── Persistence ──────────────────────────────────────────────────────────────


class TestPersist:
    def _catalog(self, postgres: bool = True, pool: Any = None) -> SimpleNamespace:
        async def _noop_init() -> None:
            return None

        return SimpleNamespace(
            _health=SimpleNamespace(postgres=postgres),
            _pg_pool=pool or Mock(),
            _init_stores=_noop_init,
        )

    async def test_skips_when_postgres_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_PERSIST", True)
        with patch(
            "app.catalog.service.get_catalog",
            return_value=self._catalog(postgres=False),
        ):
            counts = await tg_news.persist_articles([_article("H")])
        assert counts == {"ingested": 0, "duplicate": 0, "errors": 0}

    async def test_skips_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_PERSIST", False)
        counts = await tg_news.persist_articles([_article("H")])
        assert counts["ingested"] == 0

    async def test_fails_open_on_pool_outage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_PERSIST", True)

        class BoomPool:
            # asyncpg-style: acquire() returns a proxy (regular method, not a coroutine)
            def acquire(self) -> BoomPool:
                raise ConnectionError("postgres down")

        with patch(
            "app.catalog.service.get_catalog",
            return_value=self._catalog(pool=BoomPool()),
        ):
            counts = await tg_news.persist_articles([_article("H")])
        assert counts["errors"] == 1
        assert counts["ingested"] == 0

    async def test_duplicate_rows_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_PERSIST", True)

        class FakeConn:
            async def __aenter__(self) -> FakeConn:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

            async def fetchval(self, query: str, *params: Any) -> Any:
                return 1  # already ingested

        class FakePool:
            def acquire(self) -> FakeConn:
                return FakeConn()

        with patch(
            "app.catalog.service.get_catalog",
            return_value=self._catalog(pool=FakePool()),
        ):
            counts = await tg_news.persist_articles([_article("H")])
        assert counts["duplicate"] == 1
        assert counts["ingested"] == 0


# ── Scheduled job ────────────────────────────────────────────────────────────


def _stub_importance(monkeypatch: pytest.MonkeyPatch, importance: float = 0.5) -> None:
    """Stub the rerank importance gate: every article scores *importance*.

    Defaults to a news-band score (0.5 → routes to the news channel) so the
    post-job tests stay on the news lane; pass ``importance=0.7``+ for
    main-band stories and rely on the alpha-category override for alpha
    routing (alpha articles route to alpha below the main gate).

    Keeps the post-job tests deterministic (no live Infinity /rerank call)
    while exercising the wiring through ``score_importance`` →
    ``select_important`` → post path.
    """

    async def _fake_score(articles: list[NewsArticle], focus_keywords: Any = None) -> list[ScoredArticle]:
        return [ScoredArticle(article=a, importance=importance, best_query="stub") for a in articles]

    monkeypatch.setattr(tg_news, "score_importance", _fake_score)


class TestPostJob:
    def _ctx(self) -> tuple[Mock, AsyncMock]:
        bot = AsyncMock()
        ctx = Mock()
        ctx.bot = bot
        return ctx, bot

    async def test_posts_fresh_digest_and_dedupes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[_article("Fresh headline")])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 1, "duplicate": 0, "errors": 0})
            ),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
            assert await tg_news.post_tg_news_update(ctx) == 0  # deduped on second tick
        assert bot.send_message.await_count == 1
        assert "Fresh headline" in bot.send_message.await_args.kwargs["text"]

    async def test_skips_when_channel_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: None)
        assert await tg_news.post_tg_news_update(ctx) == 0
        bot.send_message.assert_not_awaited()

    async def test_skips_when_sweep_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        with patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[])):
            assert await tg_news.post_tg_news_update(ctx) == 0
        bot.send_message.assert_not_awaited()

    async def test_persist_failure_still_posts_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[_article("Headline")])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 0, "duplicate": 0, "errors": 1})
            ),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        bot.send_message.assert_awaited_once()


class TestPostVariety:
    """Breaking top story → solo post; otherwise a numbered digest."""

    def _ctx(self) -> tuple[Mock, AsyncMock]:
        bot = AsyncMock()
        ctx = Mock()
        ctx.bot = bot
        return ctx, bot

    async def test_breaking_top_story_posts_solo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        breaking = _article("🚨 BREAKING: SEC approves spot Bitcoin ETF")
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[breaking])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 1, "duplicate": 0, "errors": 0})
            ),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.kwargs["text"]
        assert "BREAKING" in text
        assert "SEC approves spot Bitcoin ETF" in text
        assert "RugMunch News" not in text  # solo post, not the digest header

    async def test_breaking_solo_links_official_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        breaking = _article(
            "JUST IN: BlackRock files for new ETF",
            body="JUST IN: BlackRock files for a new spot ETF. Details: https://coindesk.com/news/blackrock-etf",
        )
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[breaking])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 1, "duplicate": 0, "errors": 0})
            ),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        text = bot.send_message.await_args.kwargs["text"]
        assert 'href="https://coindesk.com/news/blackrock-etf"' in text

    async def test_non_breaking_top_story_posts_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        first = _article("ETF record inflows continue", message_id=10)
        second = _article("Solana upgrade ships on schedule", username="solana_news", message_id=11)
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[first, second])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 2, "duplicate": 0, "errors": 0})
            ),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        bot.send_message.assert_awaited_once()
        text = bot.send_message.await_args.kwargs["text"]
        assert "RugMunch News" in text  # digest header
        assert "ETF record inflows continue" in text
        assert "Solana upgrade ships on schedule" in text

    async def test_single_fresh_story_posts_feature_card(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """One fresh story → solo ⭐ feature card (hook + lede), not a digest."""
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        solo = _article(
            "Bitcoin crosses $120k",
            body="Bitcoin crossed $120k for the first time. Institutions piled in all week. Image: https://img.example.com/btc.jpg",
            message_id=20,
        )
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[solo])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 1, "duplicate": 0, "errors": 0})
            ),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        # The card carries an image → posted via send_photo with the caption.
        bot.send_photo.assert_awaited_once()
        caption = bot.send_photo.await_args.kwargs["caption"]
        assert "Top Story" in caption
        assert "Bitcoin crosses $120k" in caption
        assert "Bitcoin crossed $120k for the first time." in caption  # one-line hook
        assert "Institutions piled in all week." in caption  # longer lede
        assert bot.send_photo.await_args.kwargs["photo"] == "https://img.example.com/btc.jpg"

    async def test_shared_token_posts_themed_roundup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """≥2 stories mentioning the same $TOKEN → themed 🧵 roundup."""
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        first = _article("$BTC ETF inflows hit a record", body="$BTC funds saw record inflows today.", message_id=30)
        second = _article(
            "Whale moves 5k $BTC",
            body="A whale shifted 5,000 $BTC to cold storage.",
            username="whale_news",
            message_id=31,
        )
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[first, second])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 2, "duplicate": 0, "errors": 0})
            ),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        text = bot.send_message.await_args.kwargs["text"]
        assert "BTC Roundup" in text
        # Headlines are link-wrapped → assert titles (not contiguous "1. ...").
        assert "$BTC ETF inflows hit a record" in text
        assert "Whale moves 5k $BTC" in text
        assert "1." in text
        assert "2." in text

    async def test_four_plus_stories_rotate_digest_vs_hybrid_by_parity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """4+ stories alternate digest ↔ hybrid deterministically (batch parity)."""
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")

        async def _run(articles: list[NewsArticle]) -> str:
            with (
                patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=articles)),
                patch.object(
                    tg_news,
                    "persist_articles",
                    new=AsyncMock(return_value={"ingested": len(articles), "duplicate": 0, "errors": 0}),
                ),
            ):
                assert await tg_news.post_tg_news_update(ctx) == 1
            return bot.send_message.await_args.kwargs["text"]

        def _batch(prefix: str) -> list[NewsArticle]:
            return [
                _article(
                    f"Story {i} {prefix}", body=f"Details at https://news.example/{prefix}{i}.", message_id=100 + i
                )
                for i in range(5)
            ]

        even_batch = _batch("e")  # crc32 parity 0 → digest
        odd_batch = _batch("d")  # crc32 parity 1 → hybrid
        # Sanity: the parity signal really differs for the two batches.
        assert tg_news._batch_parity([tg_news._article_to_post_dict(a) for a in even_batch]) == 0
        assert tg_news._batch_parity([tg_news._article_to_post_dict(a) for a in odd_batch]) == 1

        digest_text = await _run(even_batch)
        assert "RugMunch News" in digest_text
        assert "More:" not in digest_text  # full numbered digest, no hybrid lead

        hybrid_text = await _run(odd_batch)
        assert "More:" in hybrid_text  # top-3 hybrid lead card
        assert "⭐" in hybrid_text

    async def test_high_importance_non_breaking_posts_feature_card_to_main(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Importance ≥0.7 solo path fires with a compelling card, not a bare link."""
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch, importance=0.9)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100main")
        big = _article(
            "SEC greenlights spot Bitcoin ETF",
            body="The SEC approved the product in a surprise move. Traders cheered the decision. Image: https://img.example.com/sec.jpg",
            message_id=40,
        )
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[big])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 1, "duplicate": 0, "errors": 0})
            ),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        bot.send_photo.assert_awaited_once()
        caption = bot.send_photo.await_args.kwargs["caption"]
        assert "Top Story" in caption
        assert "SEC greenlights spot Bitcoin ETF" in caption
        assert "The SEC approved the product in a surprise move." in caption  # one-line hook
        assert "BREAKING" not in caption  # no misleading 🚨 for a non-breaking story
        assert bot.send_photo.await_args.kwargs["photo"] == "https://img.example.com/sec.jpg"


# ── Channel categorization (news / alpha / scam / other) ─────────────────────


class TestChannelCategorization:
    def test_classify_news(self) -> None:
        assert tg_news.classify_channel_category("CoinTelegraph", "cointelegraph") == "news"
        assert tg_news.classify_channel_category("RMI Crypto News", "rmicryptonews") == "news"
        assert tg_news.classify_channel_category("The Block", "theblock") == "news"

    def test_classify_alpha(self) -> None:
        assert tg_news.classify_channel_category("Whale Calls Signals", "whalecalls") == "alpha"
        assert tg_news.classify_channel_category("Gem Hunter 100x", "gemhunter") == "alpha"
        assert tg_news.classify_channel_category("Alpha Signals", "alphasignals") == "alpha"

    def test_classify_scam_reuses_existing_categorization(self) -> None:
        # Unambiguous scam signals reuse scam_watch's taxonomy (no duplication).
        assert tg_news.classify_channel_category("Pump Group PND", "pumppro") == "scam"
        assert tg_news.classify_channel_category("Seed Phrase Recovery", "seedhelp") == "scam"
        assert tg_news.classify_channel_category("Fake Airdrop Claim", "airdropscam") == "scam"

    def test_classify_other(self) -> None:
        assert tg_news.classify_channel_category("Random Memes", "randommemes") == "other"
        assert tg_news.classify_channel_category("", "") == "other"

    def test_classify_own_channels_excluded(self) -> None:
        assert tg_news.classify_channel_category("RugMunch Alerts", "rmialerts") == "other"


# ── Discovered-source categorization (full-account coverage) ─────────────────


class _DiscoveryClient:
    """Fake exposing get_dialogs (discovery) + read_messages (reader protocol)."""

    def __init__(self, dialogs: list[tuple[str, str]], messages: dict[str, list[ChannelMessage]]) -> None:
        self._dialogs = dialogs
        self.messages = messages

    async def get_dialogs(self, limit: int = 500) -> Any:
        items = [
            SimpleNamespace(entity=SimpleNamespace(username=username, title=title), name=title)
            for username, title in self._dialogs
        ]
        return SimpleNamespace(dialogs=items[:limit])

    async def read_messages(self, channel_username: str, limit: int) -> list[ChannelMessage]:
        return self.messages.get(channel_username, [])[:limit]


class TestDiscoveredSources:
    def _client(self) -> _DiscoveryClient:
        return _DiscoveryClient(
            [
                ("cointelegraph", "CoinTelegraph"),  # news
                ("wublockchain", "Wu Blockchain"),  # news
                ("whalecalls", "Whale Calls Signals"),  # alpha
                ("gemhunter", "Gem Hunter 100x"),  # alpha
                ("pumppro", "Pump Group PND"),  # scam → excluded
                ("randommemes", "Random Memes"),  # other → excluded
            ],
            {},
        )

    async def test_categorizes_and_filters_discovered_channels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_AUTO_DISCOVER", True)
        sources = await tg_news.resolve_channel_sources(self._client())
        usernames = {s["username"] for s in sources}
        assert "cointelegraph" in usernames  # news kept
        assert "whalecalls" in usernames  # alpha kept
        assert "pumppro" not in usernames  # scam → scam_watch, not duplicated
        assert "randommemes" not in usernames  # other → not news-relevant
        assert {s["username"]: s["category"] for s in sources}["whalecalls"] == "alpha"
        assert {s["username"]: s["category"] for s in sources}["cointelegraph"] == "crypto_news"

    async def test_discovered_channels_bounded_by_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_AUTO_DISCOVER", True)
        monkeypatch.setattr(tg_news, "TG_NEWS_MAX_CHANNELS", 2)
        sources = await tg_news.resolve_channel_sources(self._client())
        assert len(sources) <= 2

    async def test_sweep_reads_categorized_channels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_AUTO_DISCOVER", True)
        client = _DiscoveryClient(
            [
                ("cointelegraph", "CoinTelegraph"),
                ("whalecalls", "Whale Calls Signals"),
            ],
            {
                "cointelegraph": [
                    _msg("Bitcoin ETF inflows hit a record high today as institutions pile in", message_id=1)
                ],
                "whalecalls": [_msg("Whale moved 5,000 BTC to cold storage", username="whalecalls", message_id=2)],
            },
        )
        articles = await tg_news.sweep_telegram_news(client=client, lookback_hours=6, per_channel_limit=10)
        assert len(articles) == 2
        assert {a.category for a in articles} == {"crypto_news", "alpha"}


# ── Cache injection + market-awareness snapshot (fail-open) ─────────────────


class TestCacheAndSnapshot:
    async def test_inject_into_news_cache_fail_open_redis_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "get_redis_async", lambda: None)
        n = await tg_news.inject_into_news_cache([_article("Headline")])
        assert n == 0

    async def test_publish_market_awareness_fail_open_redis_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "get_redis_async", lambda: None)
        scored = [ScoredArticle(article=_article("Headline"), importance=0.9)]
        ok = await tg_news.publish_market_awareness([_article("Headline")], scored)
        assert ok is False

    async def test_publish_market_awareness_empty_skips(self) -> None:
        assert await tg_news.publish_market_awareness([], []) is False


# ── RAG ingestion (additive, fail-open) ─────────────────────────────────────


class TestRagIngest:
    async def test_empty_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_RAG", True)
        assert await tg_news.ingest_into_rag([]) == {
            "stories": 0,
            "ingested": 0,
            "duplicates": 0,
            "errors": 0,
        }

    async def test_disabled_gate_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_RAG", False)
        assert await tg_news.ingest_into_rag([_article("Headline")]) == {
            "stories": 0,
            "ingested": 0,
            "duplicates": 0,
            "errors": 0,
        }

    async def test_fails_open_when_rag_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_RAG", True)
        import app.rag.ingestion as rag_ingestion

        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("embedder down")

        monkeypatch.setattr(rag_ingestion, "BatchIngestor", _boom)
        counts = await tg_news.ingest_into_rag([_article("Headline")])
        assert counts["errors"] == 1  # fail-open: whole batch counted as error
        assert counts["ingested"] == 0

    async def test_ingests_story_docs_into_news_v2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(tg_news, "TG_NEWS_RAG", True)

        class FakeReport:
            def __init__(self, *, accepted: int = 0, duplicate: int = 0, errors: list[str] | None = None) -> None:
                self.accepted = accepted
                self.duplicate = duplicate
                self.errors = errors or []

        class FakeIngestor:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, str]] = []

            async def ingest(
                self, content: str, collection: str, doc_id: str, metadata: dict[str, Any], source_type: str
            ) -> FakeReport:
                self.calls.append((collection, doc_id, source_type))
                return FakeReport(accepted=1)

        fake = FakeIngestor()
        import app.rag.ingestion as rag_ingestion

        monkeypatch.setattr(rag_ingestion, "BatchIngestor", lambda: fake)
        counts = await tg_news.ingest_into_rag([_article("Headline")])
        assert counts["ingested"] == 1
        assert counts["errors"] == 0
        assert fake.calls, "ingestor must be called"
        assert fake.calls[0][0] == "news_v2"
        assert fake.calls[0][2] == "news"  # source_type → news chunking

    async def test_post_job_calls_rag_ingest_after_posting(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The scheduler must feed RAG after posting (additive side-effect)."""
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        rag_mock = AsyncMock(return_value={"stories": 1, "ingested": 1, "duplicates": 0, "errors": 0})
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[_article("Fresh headline")])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 1, "duplicate": 0, "errors": 0})
            ),
            patch.object(tg_news, "ingest_into_rag", new=rag_mock),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        rag_mock.assert_awaited_once()
        bot.send_message.assert_awaited_once()

    def _ctx(self) -> tuple[Mock, AsyncMock]:
        bot = AsyncMock()
        ctx = Mock()
        ctx.bot = bot
        return ctx, bot


# ── Routing: news → news channel, alpha → alpha channel ──────────────────────


class TestRouting:
    def _ctx(self) -> tuple[Mock, AsyncMock]:
        bot = AsyncMock()
        ctx = Mock()
        ctx.bot = bot
        return ctx, bot

    def _alpha_article(self) -> NewsArticle:
        return _article(
            "Whale moves 5k BTC",
            channel="Whale Calls",
            username="whalecalls",
            message_id=9,
        )

    async def test_posts_news_and_alpha_digests(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        alpha_art = self._alpha_article()
        alpha_art.category = "alpha"
        news_art = _article("ETF record inflows", message_id=10)
        monkeypatch.setattr(
            channels_scheduler,
            "_chat_id",
            lambda channel: "-100news" if channel == "news" else "-100alpha",
        )
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[news_art, alpha_art])),
            patch.object(
                tg_news,
                "persist_articles",
                new=AsyncMock(return_value={"ingested": 2, "duplicate": 0, "errors": 0}),
            ),
            patch.object(tg_news, "inject_into_news_cache", new=AsyncMock(return_value=2)),
            patch.object(tg_news, "publish_market_awareness", new=AsyncMock(return_value=True)),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        assert bot.send_message.await_count == 2
        chats = {c.kwargs["chat_id"] for c in bot.send_message.await_args_list}
        assert chats == {"-100news", "-100alpha"}

    async def test_second_tick_dedupes_both_channels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        alpha_art = self._alpha_article()
        alpha_art.category = "alpha"
        news_art = _article("ETF record inflows", message_id=10)
        monkeypatch.setattr(
            channels_scheduler,
            "_chat_id",
            lambda channel: "-100news" if channel == "news" else "-100alpha",
        )
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[news_art, alpha_art])),
            patch.object(
                tg_news,
                "persist_articles",
                new=AsyncMock(return_value={"ingested": 2, "duplicate": 0, "errors": 0}),
            ),
            patch.object(tg_news, "inject_into_news_cache", new=AsyncMock(return_value=2)),
            patch.object(tg_news, "publish_market_awareness", new=AsyncMock(return_value=True)),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
            assert await tg_news.post_tg_news_update(ctx) == 0  # deduped on second tick
        # Tick 1 posts both channels (2 sends); tick 2 posts nothing.
        assert bot.send_message.await_count == 2

    async def test_alpha_only_when_news_chat_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
        _stub_importance(monkeypatch)
        alpha_art = self._alpha_article()
        alpha_art.category = "alpha"
        monkeypatch.setattr(
            channels_scheduler,
            "_chat_id",
            lambda channel: "-100alpha" if channel == "alpha" else None,
        )
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[alpha_art])),
            patch.object(
                tg_news,
                "persist_articles",
                new=AsyncMock(return_value={"ingested": 1, "duplicate": 0, "errors": 0}),
            ),
            patch.object(tg_news, "inject_into_news_cache", new=AsyncMock(return_value=1)),
            patch.object(tg_news, "publish_market_awareness", new=AsyncMock(return_value=True)),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        assert bot.send_message.await_count == 1
        assert bot.send_message.await_args.kwargs["chat_id"] == "-100alpha"


# ── Registration ─────────────────────────────────────────────────────────────


class FakeJobQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def run_repeating(self, callback: Any, interval: Any, first: Any = None, name: Any = None, **kwargs: Any) -> None:
        self.jobs.append({"callback": callback, "interval": interval, "first": first, "name": name})


class TestRegistration:
    def test_registers_tg_news_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "ALERTS_CHANNEL_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "ALPHA_CHANNEL_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "TG_NEWS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        names = {j["name"] for j in fake_jq.jobs}
        assert "channel_tg_news" in names
        tg_job = next(j for j in fake_jq.jobs if j["name"] == "channel_tg_news")
        # The scheduler owns the tg_news cadence (tuned default 20 min) — not
        # the telegram_news source module's 30-min ``TG_NEWS_INTERVAL_MINUTES``.
        assert tg_job["interval"] == channels_scheduler._TG_NEWS_INTERVAL_MINUTES * 60

    def test_tg_news_job_skipped_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "TG_NEWS_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        assert "channel_tg_news" not in {j["name"] for j in fake_jq.jobs}


# ── Headless-client availability probe (regression: boot-before-install) ────


class TestHeadlessProbe:
    """The telethon availability probe must re-probe after a failed check.

    Regression for the prod incident where the bot booted BEFORE telethon was
    pip-installed into the venv: the old ``if _telethon_available is None``
    guard cached the negative result forever, so every tg_news sweep logged
    ``headless_client_unavailable`` and returned [] even after the dependency
    was installed — until a manual service restart. The fix re-probes on every
    failed check (positive results stay cached).
    """

    async def test_reprobes_when_telethon_installed_after_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.domains.telegram.headless_client as hc

        # Simulate the boot-time state: telethon was missing at first probe.
        monkeypatch.setattr(hc, "_telethon_available", False)
        # Make the probe pass now (telethon importable after install).
        monkeypatch.setattr(hc, "get_api_credentials", lambda: ("+15550000000", 12345, "hash"))
        monkeypatch.setattr(hc, "get_session_path", lambda: "/tmp/hc_probe_test")  # noqa: S108

        class FakeTelegramClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.started = False

            async def start(self, phone: str = "") -> None:
                self.started = True

            async def get_me(self) -> Any:
                return SimpleNamespace(username="probe", first_name="Probe", id=1)

            def is_connected(self) -> bool:
                return True

            async def disconnect(self) -> None:
                return None

        # Patch the telethon module so `import telethon` succeeds and
        # TelegramClient resolves to the fake (no network I/O).
        fake_mod = types.ModuleType("telethon")
        fake_mod.TelegramClient = FakeTelegramClient
        monkeypatch.setitem(sys.modules, "telethon", fake_mod)

        client = hc.HeadlessTelegramClient()
        await client.start()

        assert hc._telethon_available is True, "probe must re-check after a prior failure"
        assert client._client is not None
        assert getattr(client._client, "started", False) is True

    async def test_negative_probe_raises_when_still_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.domains.telegram.headless_client as hc

        monkeypatch.setattr(hc, "_telethon_available", False)
        # Make `import telethon` raise ImportError (None sentinel in
        # sys.modules is treated as "import failed" by the import system).
        monkeypatch.setitem(sys.modules, "telethon", None)

        client = hc.HeadlessTelegramClient()
        with pytest.raises(RuntimeError, match="Telethon not installed"):
            await client.start()
        assert hc._telethon_available is False
