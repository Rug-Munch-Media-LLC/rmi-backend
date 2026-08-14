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

NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)


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
    def test_defaults_have_ten_handles(self) -> None:
        assert len(tg_news._DEFAULT_CHANNELS) == 10
        assert "cointelegraph" in tg_news._DEFAULT_CHANNELS
        assert "WatcherGuru" in tg_news._DEFAULT_CHANNELS

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


class TestPostJob:
    def _ctx(self) -> tuple[Mock, AsyncMock]:
        bot = AsyncMock()
        ctx = Mock()
        ctx.bot = bot
        return ctx, bot

    async def test_posts_fresh_digest_and_dedupes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = self._ctx()
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
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        with (
            patch.object(tg_news, "sweep_telegram_news", new=AsyncMock(return_value=[_article("Headline")])),
            patch.object(
                tg_news, "persist_articles", new=AsyncMock(return_value={"ingested": 0, "duplicate": 0, "errors": 1})
            ),
        ):
            assert await tg_news.post_tg_news_update(ctx) == 1
        bot.send_message.assert_awaited_once()


# ── Registration ─────────────────────────────────────────────────────────────


class FakeJobQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def run_repeating(self, callback: Any, interval: Any, first: Any = None, name: Any = None, **kwargs: Any) -> None:
        self.jobs.append({"callback": callback, "interval": interval, "first": first, "name": name})


class TestRegistration:
    def test_registers_tg_news_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "NEWS_CHANNEL_ENABLED", False)
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
        assert tg_job["interval"] == tg_news.TG_NEWS_INTERVAL_MINUTES * 60

    def test_tg_news_job_skipped_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "TG_NEWS_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        assert "channel_tg_news" not in {j["name"] for j in fake_jq.jobs}
