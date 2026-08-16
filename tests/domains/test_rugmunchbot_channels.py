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
from app.domains.telegram.rugmunchbot.channels.importance import ScoredArticle


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


def _stub_scorer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fallback: bool = False,
    scores: list[float] | None = None,
) -> None:
    """Patch ``channels_scheduler.score_importance`` with a deterministic stub.

    Mirrors the real scorer's contract: one ``ScoredArticle`` per input
    article, ``fallback=True`` meaning the reranker is down (the channel keeps
    its full window, like ``select_important``'s fail-open fallback). Avoids
    any network attempt at Infinity /rerank in tests.
    """

    async def _fake(articles: list[Any], focus_keywords: Any = None) -> list[ScoredArticle]:
        values = [0.5] * len(articles) if scores is None else list(scores)
        return [
            ScoredArticle(
                article=article,
                importance=float(values[i]) if i < len(values) else 0.5,
                best_query="stub",
                fallback=fallback,
            )
            for i, article in enumerate(articles)
        ]

    monkeypatch.setattr(channels_scheduler, "score_importance", _fake)


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

    def test_updates_role_resolves_from_registry_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env_only_resolution(monkeypatch)
        monkeypatch.setenv("RMI_UPDATES_CHANNEL_ID", "-1000003")
        assert bot_config._resolve_channel_id("updates") == "-1000003"

    def test_updates_role_resolves_none_without_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _env_only_resolution(monkeypatch)
        monkeypatch.delenv("RMI_UPDATES_CHANNEL_ID", raising=False)
        # updates has no documented fallback → resolves to None (not in CHANNELS)
        assert bot_config._resolve_channel_id("updates") is None


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


class TestBreakingFormatter:
    def test_renders_solo_breaking_with_official_link(self) -> None:
        text = channels_formatters.format_breaking_post(
            {
                "title": "SEC approves spot Bitcoin ETF",
                "summary": "The agency green-lit the long-awaited product in a surprise move.",
                "source": "CoinDesk",
                "url": "https://coindesk.com/news/sec-approves-etf",
            }
        )
        assert "BREAKING" in text
        assert "SEC approves spot Bitcoin ETF" in text
        assert 'href="https://coindesk.com/news/sec-approves-etf"' in text
        assert "@RugMunchBot" in text  # footer preserved
        assert "RugMunch News" not in text  # solo post, not the digest

    def test_escapes_and_clips_breaking_content(self) -> None:
        text = channels_formatters.format_breaking_post(
            {
                "title": "<b>evil</b> headline",
                "summary": "x" * 5000,
                "source": "x&y",
                "url": "",
            }
        )
        assert "<b>evil</b>" not in text
        assert "&lt;b&gt;evil&lt;/b&gt;" in text
        assert "x&amp;y" in text
        assert len(text) <= 4096  # Telegram soft limit


class TestFeatureFormatter:
    """Solo ⭐ top-story card: rewritten title + one-line hook + lede + image."""

    def _item(self) -> dict[str, Any]:
        return {
            "title": "BTC rips to a new all-time high as ETF inflows surge",
            "summary": "Bitcoin crossed $120k for the first time. Institutions piled in all week.",
            "source": "CoinDesk",
            "url": "https://coindesk.com/news/btc-ath",
            "canonical_url": "https://coindesk.com/news/btc-ath",
            "image_url": "https://img.coindesk.com/btc.jpg",
        }

    def test_renders_hook_and_longer_lede_not_bare_link(self) -> None:
        text = channels_formatters.format_feature_post(self._item())
        assert "Top Story" in text
        assert "BTC rips to a new all-time high" in text
        assert 'href="https://coindesk.com/news/btc-ath"' in text
        # One-line hook = first sentence; longer lede = the rest.
        assert "Bitcoin crossed $120k for the first time." in text
        assert "Institutions piled in all week." in text
        assert "CoinDesk" in text
        assert "@RugMunchBot" in text  # footer

    def test_carries_image_url(self) -> None:
        post = channels_formatters.format_feature_post(self._item())
        assert post.image_url == "https://img.coindesk.com/btc.jpg"
        assert isinstance(post, str)

    def test_no_image_no_hook_falls_back_gracefully(self) -> None:
        post = channels_formatters.format_feature_post({"title": "Lone story", "summary": "", "source": "X", "url": ""})
        assert "Lone story" in post
        assert post.image_url == ""


class TestHybridFormatter:
    """Top-3 hybrid: lead card (image + hook) + short numbered rest."""

    def _items(self) -> list[dict[str, Any]]:
        return [
            {
                "title": "Lead story",
                "summary": "First sentence of the lead. More detail here.",
                "source": "CoinDesk",
                "url": "https://example.com/lead",
                "canonical_url": "https://example.com/lead",
                "image_url": "https://example.com/lead.jpg",
            },
            {"title": "Second story", "source": "The Block", "url": "https://example.com/2"},
            {"title": "Third story", "source": "The Block", "url": "https://example.com/3"},
        ]

    def test_renders_lead_card_then_numbered_rest(self) -> None:
        text = channels_formatters.format_hybrid_post(self._items())
        assert "RugMunch News" in text  # hybrid keeps the channel header
        assert "Lead story" in text
        assert 'href="https://example.com/lead"' in text  # lead links to canonical source
        assert "First sentence of the lead." in text  # one-line hook
        assert "More:" in text
        assert "Second story" in text
        assert "Third story" in text

    def test_carries_lead_image(self) -> None:
        post = channels_formatters.format_hybrid_post(self._items())
        assert post.image_url == "https://example.com/lead.jpg"


class TestThemedFormatter:
    """🧵 themed roundup: one topic label over numbered, source-cited items."""

    def test_renders_theme_roundup_with_image(self) -> None:
        items = [
            {
                "title": "$BTC ETF inflows hit a record",
                "summary": "Institutions piled into $BTC funds.",
                "source": "CoinDesk",
                "url": "https://example.com/1",
                "image_url": "https://example.com/btc.jpg",
            },
            {"title": "$BTC whale moves 5k coins", "source": "The Block", "url": "https://example.com/2"},
        ]
        post = channels_formatters.format_themed_post(items, theme="BTC")
        assert "BTC Roundup" in post
        assert "$BTC ETF inflows hit a record" in post
        assert "$BTC whale moves 5k coins" in post
        assert 'href="https://example.com/1"' in post  # items keep canonical source links
        assert "Institutions piled into" in post  # one-line takeaway per story
        assert post.image_url == "https://example.com/btc.jpg"
        assert "@RugMunchBot" in post

    def test_escapes_theme_label(self) -> None:
        post = channels_formatters.format_themed_post(
            [{"title": "T", "source": "X", "url": ""}],
            theme="<script>",
        )
        assert "<script>" not in post
        assert "&lt;script&gt;" in post


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

    def test_renders_alert_type_condition_verdict_and_chart_link(self) -> None:
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
                    "alert_type": "scanner",
                    "condition": "risk 85/100 ≥ 70 (high)",
                }
            ]
        )
        assert "Scanner Alert" in text  # alert type
        assert "Condition met: risk 85/100 ≥ 70 (high)" in text  # met vs current
        assert "Why it matters" in text  # one-line verdict
        assert "honeypot — sells can be blocked after buying" in text
        assert "gmgn.ai" in text  # chart deep link
        assert "rugmunch.io/scanner" in text  # scan deep link

    def test_alert_verdict_known_flag_first(self) -> None:
        assert "honeypot" in channels_formatters.alert_verdict({"risk_flags": ["honeypot"]})
        assert "LP removed" in channels_formatters.alert_verdict({"risk_flags": ["lp_removed"]})
        assert "flagged by the RugMunch scanner" in channels_formatters.alert_verdict({"risk_flags": ["zzz"]})

    def test_empty_state_is_honest(self) -> None:
        text = channels_formatters.format_alerts_post([])
        assert "No new high-risk tokens" in text


class TestMainEscalationFormatter:
    def test_renders_high_signal_card_with_condition_and_verdict(self) -> None:
        post = channels_formatters.format_main_escalation(
            {
                "name": "Bridge Drain",
                "symbol": "BRDG",
                "address": "0xexploit1",
                "chain": "ethereum",
                "risk_level": "critical",
                "risk_flags": ["exploit", "drained"],
                "condition": "risk 98/100 ≥ 70 (critical)",
                "image_url": "https://img.example.com/drain.jpg",
            }
        )
        assert "HIGH SIGNAL" in post
        assert "Bridge Drain" in post
        assert "0xexploit1" in post
        assert "Condition met" in post  # headline link goes to the scanner page
        assert "Why it matters" in post
        assert "rugmunch.io/scanner" in post
        assert post.image_url == "https://img.example.com/drain.jpg"
        assert "@RugMunchBot" in post  # footer

    def test_alpha_whale_card_uses_summary_as_verdict(self) -> None:
        post = channels_formatters.format_main_escalation(
            {
                "title": "Whale: 50k SOL moved",
                "token_address": "So11111111111111111111111111111111111111112",
                "chain": "solana",
                "summary": "A dormant whale moved 50,000 SOL to a fresh wallet.",
            }
        )
        assert "HIGH SIGNAL" in post
        assert "Whale: 50k SOL moved" in post
        assert "A dormant whale moved 50,000 SOL" in post
        assert "gmgn.ai" in post  # chart link built from token_address

    def test_escapes_user_content(self) -> None:
        post = channels_formatters.format_main_escalation(
            {"name": "<script>evil</script>", "symbol": "X", "address": "", "risk_flags": []}
        )
        assert "<script>" not in post
        assert "&lt;script&gt;" in post


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

    def test_renders_verdict_and_actionable_links(self) -> None:
        post = channels_formatters.format_alpha_post([self._whale()], [], [self._launch()])
        # Launch verdict + whale chart link (built from token_address).
        assert "low scan verdict" in post
        assert "gmgn.ai" in post
        assert "rugmunch.io/scanner" in post
        # Smart-money wallet explorer for a solana wallet.
        text = channels_formatters.format_alpha_post([], [self._smart()], [])
        assert "solscan.io/account/abc123wallet" in text

    def test_alpha_post_carries_image_when_available(self) -> None:
        post = channels_formatters.format_alpha_post(
            [
                {
                    "token_address": "w",
                    "title": "Whale move",
                    "chain": "solana",
                    "image_url": "https://img.example.com/w.jpg",
                }
            ],
            [],
            [],
        )
        assert post.image_url == "https://img.example.com/w.jpg"

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
                            "image_url": "https://img.example.com/bad.jpg",
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
        # Curated-card fields the formatter renders (alert type, condition met
        # vs current, image passthrough).
        assert alerts[0]["alert_type"] == "scanner"
        assert alerts[0]["condition"] == "risk 92/100 ≥ 70 (critical)"
        assert alerts[0]["image_url"] == "https://img.example.com/bad.jpg"


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
        # Reranker down (fallback=True) → the digest keeps its full window.
        _stub_scorer(monkeypatch, fallback=True)
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

    async def test_alerts_job_escapes_reranker_fallback_keeps_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        _stub_scorer(monkeypatch, fallback=True)
        item = {
            "address": "0xbad1",
            "name": "Rug Token",
            "symbol": "RUG",
            "chain": "ethereum",
            "risk_level": "high",
            "risk_score": 85,
            "risk_flags": ["honeypot"],
        }
        with patch.object(channels_scheduler, "fetch_risk_alerts", new=AsyncMock(return_value=[item])):
            assert await channels_scheduler.post_alert_update(ctx) == 1
        bot.send_message.assert_awaited_once()
        assert "Risk Alerts" in bot.send_message.await_args.kwargs["text"]

    async def test_alerts_job_gates_below_floor_in_rerank_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        _stub_scorer(monkeypatch, fallback=False, scores=[0.1])
        item = {"address": "0xlow1", "name": "Noise", "symbol": "NOI", "risk_level": "low"}
        with patch.object(channels_scheduler, "fetch_risk_alerts", new=AsyncMock(return_value=[item])):
            assert await channels_scheduler.post_alert_update(ctx) == 0
        bot.send_message.assert_not_awaited()

    async def test_alerts_job_escalates_high_importance_to_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        # ≥0.7 (route_news main gate) → alerts digest AND solo main card.
        _stub_scorer(monkeypatch, fallback=False, scores=[0.92])
        item = {
            "address": "0xexploit1",
            "name": "Bridge Drain",
            "symbol": "BRDG",
            "chain": "ethereum",
            "risk_level": "critical",
            "risk_score": 98,
            "risk_flags": ["exploit", "drained"],
            "condition": "risk 98/100 ≥ 70 (critical)",
        }
        with patch.object(channels_scheduler, "fetch_risk_alerts", new=AsyncMock(return_value=[item])):
            assert await channels_scheduler.post_alert_update(ctx) == 2
        assert bot.send_message.await_count == 2
        texts = [call.kwargs["text"] for call in bot.send_message.await_args_list]
        assert any("Risk Alerts" in t for t in texts)
        assert any("HIGH SIGNAL" in t for t in texts)

    async def test_alpha_job_escalates_high_importance_to_main(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        _stub_scorer(monkeypatch, fallback=False, scores=[0.8])
        with patch.object(
            channels_scheduler,
            "fetch_alpha_content",
            new=AsyncMock(
                return_value={
                    "whale_moves": [{"token_address": "w", "title": "Whale: 50k SOL moved", "chain": "solana"}],
                    "smart_money": [],
                    "launches": [],
                }
            ),
        ):
            assert await channels_scheduler.post_alpha_update(ctx) == 2
        assert bot.send_message.await_count == 2
        texts = [call.kwargs["text"] for call in bot.send_message.await_args_list]
        assert any("Alpha Feed" in t for t in texts)
        assert any("HIGH SIGNAL" in t for t in texts)

    async def test_alpha_job_skips_when_channel_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: None)
        _stub_scorer(monkeypatch, fallback=True)
        with patch.object(
            channels_scheduler,
            "fetch_alpha_content",
            new=AsyncMock(return_value={"whale_moves": [], "smart_money": [], "launches": []}),
        ):
            assert await channels_scheduler.post_alpha_update(ctx) == 0
        bot.send_message.assert_not_awaited()

    async def test_safe_send_returns_false_on_telegram_error(self) -> None:
        bot = AsyncMock()
        bot.send_message.side_effect = Exception("boom")
        assert await channels_scheduler._safe_send(bot, "-100", "text") is False


class TestRegistration:
    def test_registers_all_jobs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(channels_scheduler, "CHANNEL_JOBS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "ALERTS_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "ALPHA_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "TG_NEWS_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: f"-100{channel}")
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        # The legacy ``channel_news`` job is deprecated (commented out); the
        # routed tg_news lane now owns the news channel. Every enabled job
        # with a resolvable chat id registers (scam_watch needs no chat id).
        assert {j["name"] for j in fake_jq.jobs} == {
            "channel_alerts",
            "channel_alpha",
            "channel_tg_news",
            "channel_scam_watch",
        }

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
        monkeypatch.setattr(channels_scheduler, "ALERTS_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "ALPHA_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "TG_NEWS_ENABLED", True)
        # Only news resolves a chat id → the tg_news job registers (its routed
        # lane needs at least one of news/main/alpha), alerts/alpha skip.
        # scam_watch feeds the scanner and needs no chat id, so it registers too.
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: "-100news" if channel == "news" else None)
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        assert [j["name"] for j in fake_jq.jobs] == [
            "channel_tg_news",
            "channel_scam_watch",
        ]
