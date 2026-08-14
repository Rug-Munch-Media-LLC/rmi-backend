"""Unit tests for the RugMunchBot scheduled channel content (news/alerts/alpha).

Covers:
  * config channel-id resolution (gopass → env → documented fallback)
  * per-channel HTML formatters (escaping, /scan links, honest empty states)
  * content fetchers (backend mocked at the data_resolver boundary)
  * JobQueue registration + posting jobs (dedupe, fail-open)

No live network I/O — every fetch is patched at the module boundary it
actually uses (``channels.content.backend_get_json`` /
``channels.content._collect_whale_watch_alerts``), same pattern as the rest
of the bot test suite.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domains.telegram.rugmunchbot import config as bot_config
from app.domains.telegram.rugmunchbot.channels import (
    content as channels_content,
    formatters as channels_formatters,
    scheduler as channels_scheduler,
)


@pytest.fixture(autouse=True)
def _reset_channel_state() -> None:
    """Every test starts with a clean channel cache + dedupe state."""
    bot_config.reset_channel_cache()
    channels_scheduler._posted_keys.clear()
    yield
    bot_config.reset_channel_cache()
    channels_scheduler._posted_keys.clear()


def _env_only_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make channel resolution deterministic: env → fallback (no gopass)."""

    def _fake_get_token(gopass_path: str, env_keys: list[str]) -> str:
        for key in env_keys:
            value = os.getenv(key)
            if value:
                return value
        return ""

    monkeypatch.setattr(bot_config, "_get_token", _fake_get_token)


def _ctx() -> tuple[Mock, AsyncMock]:
    bot = AsyncMock()
    ctx = Mock()
    ctx.bot = bot
    return ctx, bot


# ── Channel resolution ────────────────────────────────────────────────────


class TestChannelResolution:
    def test_keys_preserved(self) -> None:
        assert set(bot_config.CHANNELS) == {"main", "news", "alerts", "alpha", "scans"}

    def test_env_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env_only_resolution(monkeypatch)
        monkeypatch.setenv("RMI_NEWS_CHANNEL_ID", "-1009990001")
        assert bot_config._resolve_channel_id("news") == "-1009990001"

    def test_falls_back_to_documented_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env_only_resolution(monkeypatch)
        monkeypatch.delenv("RMI_ALPHA_CHANNEL_ID", raising=False)
        assert bot_config._resolve_channel_id("alpha") == bot_config._CHANNEL_DEFAULTS["alpha"]

    def test_cache_reset_applies_new_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env_only_resolution(monkeypatch)
        monkeypatch.setenv("RMI_ALERTS_CHANNEL_ID", "-1000001")
        assert bot_config._resolve_channel_id("alerts") == "-1000001"
        monkeypatch.setenv("RMI_ALERTS_CHANNEL_ID", "-1000002")
        # Still memoized until reset
        assert bot_config._resolve_channel_id("alerts") == "-1000001"
        bot_config.reset_channel_cache()
        assert bot_config._resolve_channel_id("alerts") == "-1000002"

    def test_unknown_key_returns_none(self) -> None:
        assert bot_config._resolve_channel_id("bogus") is None


# ── Formatters ────────────────────────────────────────────────────────────


class TestNewsFormatter:
    def test_renders_headlines_with_source_and_link(self) -> None:
        text = channels_formatters.format_news_post(
            [
                {"title": "BTC rips to 100k", "source": "CoinDesk", "url": "https://example.com/a"},
                {"title": "SOL breaks out", "source": "The Block", "url": "https://example.com/b"},
            ]
        )
        assert "RugMunch News" in text
        assert "BTC rips to 100k" in text
        assert "CoinDesk" in text
        assert 'href="https://example.com/a"' in text
        assert "@RugMunchBot" in text  # footer

    def test_escapes_user_content(self) -> None:
        text = channels_formatters.format_news_post(
            [{"title": "<script>alert(1)</script>", "source": "x&y", "url": ""}]
        )
        assert "<script>" not in text
        assert "&lt;script&gt;" in text
        assert "x&amp;y" in text


class TestAlertsFormatter:
    def test_renders_risk_digest_with_scan_links(self) -> None:
        text = channels_formatters.format_alerts_post(
            [
                {
                    "name": "Rug Token",
                    "symbol": "RUG",
                    "address": "0xabc123",
                    "chain": "ethereum",
                    "risk_level": "high",
                    "risk_score": 85,
                    "risk_flags": ["honeypot", "lp_removed"],
                }
            ]
        )
        assert "Risk Alerts" in text
        assert "Rug Token" in text
        assert "/scan" in text
        assert "0xabc123" in text
        assert "honeypot" in text
        assert "85" in text

    def test_empty_state_is_honest(self) -> None:
        text = channels_formatters.format_alerts_post([])
        assert "No new high-risk tokens" in text


class TestAlphaFormatter:
    def _whale(self) -> dict[str, Any]:
        return {
            "token_address": "So11111111111111111111111111111111111111112",
            "token_symbol": "SOL",
            "title": "Whale: 12,000 SOL moved",
            "chain": "solana",
            "source": "whale_watcher",
        }

    def _smart(self) -> dict[str, Any]:
        return {"address": "abc123wallet", "label": "Smart Whale", "tags": ["whale", "accumulator"], "pnl_usd": 50000}

    def _launch(self) -> dict[str, Any]:
        return {"address": "0xlaunch1", "name": "NewCoin", "symbol": "NEW", "chain": "solana", "risk_level": "low"}

    def test_renders_all_sections(self) -> None:
        text = channels_formatters.format_alpha_post([self._whale()], [self._smart()], [self._launch()])
        assert "Alpha Feed" in text
        assert "Whale Moves" in text
        assert "Smart Money" in text
        assert "New Launches" in text
        assert "/scan" in text
        assert "Smart Whale" in text
        assert "NewCoin" in text

    def test_empty_sections_are_omitted_not_invented(self) -> None:
        text = channels_formatters.format_alpha_post([], [], [])
        assert "No fresh alpha signals" in text
        assert "Whale Moves" not in text
        assert "Smart Money" not in text

    def test_escapes_wallet_labels(self) -> None:
        text = channels_formatters.format_alpha_post([], [{"address": "x", "label": "<b>evil</b>"}], [])
        assert "<b>evil</b>" not in text
        assert "&lt;b&gt;evil&lt;/b&gt;" in text


# ── Content fetchers (backend mocked) ─────────────────────────────────────


class TestContentFetchers:
    async def test_fetch_trending_news_normalizes_and_skips_malformed(self) -> None:
        with patch.object(
            channels_content,
            "backend_get_json",
            new=AsyncMock(
                return_value={
                    "items": [
                        {"title": "Headline A", "url": "https://a.example", "source": "A", "summary": "sum"},
                        {"not": "a dict field"},
                        {"title": "", "url": "https://b.example", "source": "B"},
                    ]
                }
            ),
        ):
            items = await channels_content.fetch_trending_news(limit=5)
        assert len(items) == 1
        assert items[0]["title"] == "Headline A"
        assert items[0]["factual_rating"] == ""

    async def test_fetch_trending_news_empty_on_backend_miss(self) -> None:
        with patch.object(channels_content, "backend_get_json", new=AsyncMock(return_value=None)):
            assert await channels_content.fetch_trending_news() == []

    async def test_fetch_alpha_content_composes_three_sources(self) -> None:
        with (
            patch.object(channels_content, "_collect_whale_watch_alerts", new=AsyncMock(return_value=[])),
            patch.object(
                channels_content,
                "backend_get_json",
                new=AsyncMock(
                    side_effect=[
                        {"smart_money_wallets": [{"address": "w1", "label": "Whale"}]},
                        {"launches": [{"address": "0xL", "name": "NewCoin", "symbol": "NEW"}]},
                    ]
                ),
            ),
        ):
            content = await channels_content.fetch_alpha_content()
        assert set(content) == {"whale_moves", "smart_money", "launches"}
        assert content["whale_moves"] == []
        assert content["smart_money"][0]["address"] == "w1"
        assert content["launches"][0]["address"] == "0xL"

    async def test_fetch_risk_alerts_merges_security_flags(self) -> None:
        with patch.object(
            channels_content,
            "backend_get_json",
            new=AsyncMock(
                return_value={
                    "alerts": [
                        {
                            "address": "0xbad",
                            "name": "Bad Token",
                            "symbol": "BAD",
                            "chain": "bsc",
                            "risk_level": "critical",
                            "risk_score": 92,
                            "risk_flags": ["honeypot"],
                            "security": {"mintable": True, "lp_locked": False},
                        }
                    ]
                }
            ),
        ):
            alerts = await channels_content.fetch_risk_alerts()
        assert len(alerts) == 1
        assert "honeypot" in alerts[0]["risk_flags"]
        assert "mintable" in alerts[0]["risk_flags"]
        assert "lp_locked" not in alerts[0]["risk_flags"]


# ── Scheduler ─────────────────────────────────────────────────────────────


class FakeJobQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def run_repeating(self, callback: Any, interval: Any, first: Any = None, name: Any = None, **kwargs: Any) -> None:
        self.jobs.append({"callback": callback, "interval": interval, "first": first, "name": name})


class TestPostingJobs:
    async def test_news_job_sends_and_dedupes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        item = {"title": "Only headline", "source": "S", "url": "https://same.example"}
        with patch.object(channels_scheduler, "fetch_trending_news", new=AsyncMock(return_value=[item])):
            assert await channels_scheduler.post_news_update(ctx) == 1
            assert await channels_scheduler.post_news_update(ctx) == 0  # deduped
        assert bot.send_message.await_count == 1

    async def test_news_job_skips_when_channel_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: None)
        with patch.object(channels_scheduler, "fetch_trending_news", new=AsyncMock(return_value=[])):
            assert await channels_scheduler.post_news_update(ctx) == 0
        bot.send_message.assert_not_awaited()

    async def test_alpha_job_sends_composed_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        with patch.object(
            channels_scheduler,
            "fetch_alpha_content",
            new=AsyncMock(
                return_value={
                    "whale_moves": [{"token_address": "w", "title": "Whale move", "chain": "solana"}],
                    "smart_money": [{"address": "s1", "label": "Whale"}],
                    "launches": [],
                }
            ),
        ):
            assert await channels_scheduler.post_alpha_update(ctx) == 1
        bot.send_message.assert_awaited_once()
        assert "Whale Moves" in bot.send_message.await_args.kwargs["text"]

    async def test_safe_send_returns_false_on_telegram_error(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("boom")
        assert await channels_scheduler._safe_send(bot, "-100", "text") is False


class TestRegistration:
    def test_registers_all_three_jobs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "NEWS_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "ALERTS_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "ALPHA_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: f"-100{channel}")
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        assert {j["name"] for j in fake_jq.jobs} == {"channel_news", "channel_alerts", "channel_alpha"}

    def test_master_switch_disables_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", False)
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        assert fake_jq.jobs == []

    def test_missing_job_queue_skips(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        app = SimpleNamespace(job_queue=None)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        # No exception raised, nothing registered.
        assert True

    def test_unset_channel_skips_that_job(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "NEWS_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "ALERTS_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "ALPHA_CHANNEL_ENABLED", True)
        # Only news resolves a chat id → only the news job is registered.
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news" if channel == "news" else None)
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        assert [j["name"] for j in fake_jq.jobs] == ["channel_news"]
