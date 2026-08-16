"""Unit tests for scheduler durability / retry / observability hardening.

Covers (``channels/scheduler_state.py`` + ``channels/scheduler.py``):
  * Redis-backed posted-key dedupe — save/load round-trip hydrates the
    scheduler's in-memory ``_posted_keys`` so a restart resumes cleanly
  * last-run timestamps persisted to Redis (HASH) and restored on load
  * fire-and-forget durable persist from ``_remember`` at post time
  * fail-open when Redis is unavailable (in-memory behavior preserved)
  * ``_safe_send`` retry policy (``RMI_POST_RETRIES`` + exponential backoff)
  * per-tick observability: ``channels:tick`` JSON line with the six fields
  * the tick wrapper records last-run + persists state at end of tick

No live Redis — ``get_redis_async`` is patched at the module boundary with
an in-memory fake (same pattern as the rest of the bot test suite).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from telegram.error import TelegramError

from app.domains.telegram.rugmunchbot.channels import (
    scheduler as channels_scheduler,
    scheduler_state,
)

# ── In-memory async Redis stand-in ──────────────────────────────────────────


class FakeRedis:
    """Minimal async Redis fake for the state layer's command subset."""

    def __init__(self) -> None:
        self._data: dict[str, set[str]] = {}
        self._hash: dict[str, dict[str, str]] = {}
        self._expires: list[tuple[str, int]] = []

    async def sadd(self, key: str, *members: str) -> int:
        seen = self._data.setdefault(key, set())
        before = len(seen)
        seen.update(members)
        return len(seen) - before

    async def smembers(self, key: str) -> set[str]:
        return set(self._data.get(key, set()))

    async def expire(self, key: str, ttl: int) -> bool:
        self._expires.append((key, ttl))
        return key in self._data or key in self._hash

    async def hset(self, key: str, mapping: dict[str, str] | None = None, **kwargs: str) -> int:
        merged = dict(mapping or {})
        merged.update(kwargs)
        store = self._hash.setdefault(key, {})
        before = len(store)
        store.update(merged)
        return len(store) - before

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hash.get(key, {}))

    async def scan_iter(self, match: str = "*", count: int = 100) -> Any:
        prefix = match[:-1] if match.endswith("*") else match
        for key in sorted(self._data):
            if key.startswith(prefix):
                yield key

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, fake: FakeRedis) -> None:
        self._fake = fake
        self._sadds: list[tuple[str, tuple[str, ...]]] = []
        self._expires: list[tuple[str, int]] = []
        self._hsets: list[tuple[str, dict[str, str]]] = []

    def sadd(self, key: str, *members: str) -> FakePipeline:
        self._sadds.append((key, members))
        return self

    def expire(self, key: str, ttl: int) -> FakePipeline:
        self._expires.append((key, ttl))
        return self

    def hset(self, key: str, mapping: dict[str, str] | None = None, **kwargs: str) -> FakePipeline:
        merged = dict(mapping or {})
        merged.update(kwargs)
        self._hsets.append((key, merged))
        return self

    async def execute(self) -> list[Any]:
        for key, members in self._sadds:
            await self._fake.sadd(key, *members)
        for key, ttl in self._expires:
            await self._fake.expire(key, ttl)
        for key, mapping in self._hsets:
            await self._fake.hset(key, mapping=mapping)
        return []


def _activate_fake(monkeypatch: pytest.MonkeyPatch, fake: FakeRedis) -> None:
    monkeypatch.setattr(scheduler_state, "get_redis_async", lambda: fake)


@pytest.fixture(autouse=True)
def _reset_state() -> None:
    scheduler_state.reset_state()
    channels_scheduler._posted_keys.clear()
    yield
    scheduler_state.reset_state()
    channels_scheduler._posted_keys.clear()


def _ctx() -> tuple[Mock, AsyncMock]:
    bot = AsyncMock()
    ctx = Mock()
    ctx.bot = bot
    return ctx, bot


# ── Durability: save / load round-trip ──────────────────────────────────────


class TestStateRoundTrip:
    async def test_save_persists_posted_keys_and_last_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        _activate_fake(monkeypatch, fake)
        channels_scheduler._posted_keys["news"] = {"news:https://a.example", "news:https://b.example"}
        scheduler_state.mark_last_run("channel_news")
        assert await scheduler_state.save_state(channels_scheduler._posted_keys, job="channel_news") is True
        assert await fake.smembers("bot:channels:posted:news") == {"news:https://a.example", "news:https://b.example"}
        last = await fake.hgetall("bot:channels:last_run")
        assert "channel_news" in last
        assert fake._expires  # TTLs were refreshed

    async def test_load_hydrates_seed_and_last_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        await fake.sadd("bot:channels:posted:news", "news:https://a.example", "news:https://b.example")
        await fake.sadd("bot:channels:posted:tgnews", "tgnews:https://t.me/ct/1")
        await fake.hset("bot:channels:last_run", mapping={"channel_news": "2026-08-14T12:00:00+00:00"})
        _activate_fake(monkeypatch, fake)
        seed: dict[str, set[str]] = {}
        assert await scheduler_state.load_state(seed) is True
        assert seed["news"] == {"news:https://a.example", "news:https://b.example"}
        assert seed["tgnews"] == {"tgnews:https://t.me/ct/1"}
        assert scheduler_state.last_run_iso("channel_news") == "2026-08-14T12:00:00+00:00"

    async def test_save_then_load_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        _activate_fake(monkeypatch, fake)
        channels_scheduler._posted_keys["alpha:whale"] = {"whale:0xabc"}
        scheduler_state.mark_last_run("channel_alpha")
        assert await scheduler_state.save_state(channels_scheduler._posted_keys, job="channel_alpha") is True
        scheduler_state.reset_state()
        seed: dict[str, set[str]] = {}
        assert await scheduler_state.load_state(seed) is True
        assert seed["alpha:whale"] == {"whale:0xabc"}
        assert scheduler_state.last_run_iso("channel_alpha") != ""

    async def test_remember_persists_durably(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        _activate_fake(monkeypatch, fake)
        assert channels_scheduler._remember("news", "news:https://a.example") is True
        await asyncio.sleep(0.01)  # let the fire-and-forget persist task run
        assert await fake.smembers("bot:channels:posted:news") == {"news:https://a.example"}
        assert await fake.smembers("bot:channels:posted:news") == {"news:https://a.example"}


# ── Fail-open when Redis is down ────────────────────────────────────────────


class TestFailOpen:
    async def test_state_ops_never_raise_when_redis_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scheduler_state, "get_redis_async", lambda: None)
        assert scheduler_state.is_available() is False
        assert await scheduler_state.load_state({}) is False
        assert await scheduler_state.save_state({}) is False

    async def test_remember_still_dedupes_in_memory_when_redis_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(scheduler_state, "get_redis_async", lambda: None)
        assert channels_scheduler._remember("news", "k1") is True
        assert channels_scheduler._remember("news", "k1") is False  # in-memory dedupe preserved
        assert channels_scheduler._remember("news", "k2") is True


# ── Retry policy ────────────────────────────────────────────────────────────


class TestSafeSendRetry:
    async def test_retries_then_gives_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("boom")
        monkeypatch.setattr(channels_scheduler, "_POST_RETRIES", 2)
        monkeypatch.setattr(channels_scheduler, "_RETRY_BASE_DELAY", 0.001)
        monkeypatch.setattr(channels_scheduler, "_RETRY_MAX_DELAY", 0.01)
        assert await channels_scheduler._safe_send(bot, "-100", "text") is False
        assert bot.send_message.await_count == 3  # initial + 2 retries

    async def test_succeeds_on_second_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bot = AsyncMock()
        attempts = {"n": 0}

        async def _flaky(*args: Any, **kwargs: Any) -> Any:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise TelegramError("temporary failure")
            return True

        bot.send_message.side_effect = _flaky
        monkeypatch.setattr(channels_scheduler, "_POST_RETRIES", 2)
        monkeypatch.setattr(channels_scheduler, "_RETRY_BASE_DELAY", 0.001)
        monkeypatch.setattr(channels_scheduler, "_RETRY_MAX_DELAY", 0.01)
        assert await channels_scheduler._safe_send(bot, "-100", "text") is True
        assert bot.send_message.await_count == 2

    async def test_zero_retries_means_single_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("boom")
        monkeypatch.setattr(channels_scheduler, "_POST_RETRIES", 0)
        assert await channels_scheduler._safe_send(bot, "-100", "text") is False
        assert bot.send_message.await_count == 1


# ── Observability ───────────────────────────────────────────────────────────


class TestObservability:
    async def test_news_job_emits_tick_line_with_full_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, _ = _ctx()
        item = {"title": "Headline", "source": "S", "url": "https://x.example"}
        logger_mock = Mock()
        monkeypatch.setattr(channels_scheduler, "logger", logger_mock)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news")
        with patch.object(channels_scheduler, "fetch_trending_news", new=AsyncMock(return_value=[item])):
            assert await channels_scheduler.post_news_update(ctx) == 1
        ticks = [c for c in logger_mock.info.call_args_list if c.args and c.args[0] == "channels:tick"]
        assert len(ticks) == 1
        kwargs = ticks[0].kwargs
        assert kwargs["job"] == "channel_news"
        assert kwargs["items_swept"] == 1
        assert kwargs["items_scored"] == 1
        assert kwargs["items_posted"] == 1
        assert kwargs["items_failed"] == 0
        assert "latency_ms" in kwargs

    async def test_tick_wrapper_marks_last_run_and_persists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeRedis()
        _activate_fake(monkeypatch, fake)

        async def fake_job(ctx: Any) -> int:
            return 1

        wrapped = channels_scheduler._tick_wrapper("channel_tg_news", fake_job, envelope=True)
        assert await wrapped(None) == 1
        assert scheduler_state.last_run_iso("channel_tg_news") != ""
        last = await fake.hgetall("bot:channels:last_run")
        assert "channel_tg_news" in last

    async def test_tick_wrapper_never_raises_on_callback_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _activate_fake(monkeypatch, FakeRedis())

        async def broken_job(ctx: Any) -> int:
            raise RuntimeError("boom")

        wrapped = channels_scheduler._tick_wrapper("channel_scam_watch", broken_job, envelope=True)
        # Must swallow the exception (a poster never crashes the JobQueue)
        with patch.object(channels_scheduler.logger, "error") as err_mock:
            assert await wrapped(None) is None
        assert err_mock.call_count == 1


# ── Startup load wiring ─────────────────────────────────────────────────────


class TestRegistrationWiring:
    def test_registers_state_load_one_shot_when_queue_supports_run_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "ALERTS_CHANNEL_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "ALPHA_CHANNEL_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "TG_NEWS_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "SCAM_WATCH_ENABLED", False)
        monkeypatch.setattr(channels_scheduler, "PERSONAL_CHANNEL_ENABLED", False)

        from types import SimpleNamespace

        registered: list[str] = []

        class QueueWithRunOnce:
            def run_repeating(
                self, callback: Any, interval: Any, first: Any = None, name: Any = None, **kwargs: Any
            ) -> None:
                registered.append(name or "?")

            def run_once(self, callback: Any, when: Any, name: Any = None, **kwargs: Any) -> None:
                registered.append(name or "?")

        app = SimpleNamespace(job_queue=QueueWithRunOnce())
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        assert "channel_state_load" in registered
