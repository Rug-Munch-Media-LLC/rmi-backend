"""Unit tests for the RugMunchBot scheduled channel content (news/alerts/alpha).

Covers:
  * config channel-id resolution (gopass → env → documented fallback)
  * per-channel HTML formatters (escaping, /scan links, honest empty states)
  * content fetchers (backend mocked at the data_resolver boundary)
  * JobQueue registration + posting jobs (dedupe, fail-open)

No live network I/O — every fetch is patched at the module boundary it
actually uses (``channels.content.backend_get_json`` /
``channels.content._fetch_whale_moves`` / ``_fetch_early_entries`` /
``_fetch_smart_money``), same pattern as the rest of the bot test suite.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domains.telegram.rugmunchbot import config as bot_config
from app.domains.telegram.rugmunchbot.channels import (
    _scan_commentary as channels_scan_commentary,
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

    def test_renders_ai_why_it_matters_per_story(self) -> None:
        text = channels_formatters.format_news_post(
            [
                {
                    "title": "Third bridge exploit this week",
                    "source": "CoinDesk",
                    "url": "https://example.com/a",
                    "commentary": "this is the third exploit this week targeting the same bridge pattern",
                },
                {"title": "SOL breaks out", "source": "The Block", "url": "https://example.com/b"},
            ]
        )
        assert "Third bridge exploit this week" in text
        assert "💡" in text
        assert "third exploit this week" in text
        # Stories without commentary render no 💡 line.
        assert text.count("💡") == 1

    def test_escapes_commentary_content(self) -> None:
        text = channels_formatters.format_news_post(
            [{"title": "T", "source": "X", "url": "https://example.com/t", "commentary": "<script>alert(1)</script>"}]
        )
        # Commentary passes through _takeaway (strip_markup): tags are removed
        # entirely, never rendered as raw HTML.
        assert "<script>" not in text
        assert "alert(1)" in text


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

    def test_renders_ai_why_it_matters_commentary(self) -> None:
        text = channels_formatters.format_breaking_post(
            {
                "title": "SEC approves spot Bitcoin ETF",
                "summary": "The agency green-lit the long-awaited product in a surprise move.",
                "source": "CoinDesk",
                "url": "https://coindesk.com/news/sec-approves-etf",
                "commentary": "a spot ETF opens a regulated on-ramp for institutional capital",
            }
        )
        assert "Why it matters" in text
        assert "regulated on-ramp" in text
        assert 'href="https://coindesk.com/news/sec-approves-etf"' in text


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

    def test_renders_ai_why_it_matters_commentary(self) -> None:
        item = self._item()
        item["commentary"] = "this is the third exploit this week targeting the same bridge pattern"
        post = channels_formatters.format_feature_post(item)
        assert "Why it matters" in post
        assert "third exploit this week" in post
        assert "Institutions piled in all week." in post  # lede still renders


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


class TestScanCardFormatter:
    def test_renders_compact_card_with_verdict_score_and_scan_link(self) -> None:
        post = channels_formatters.format_scan_card(
            {
                "address": "0xscan1",
                "name": "Scan Token",
                "symbol": "SCAN",
                "chain": "solana",
                "risk_level": "critical",
                "risk_score": 96,
                "risk_flags": ["honeypot", "lp_removed"],
            }
        )
        assert "Scan of note" in post
        assert "Scan Token" in post
        assert "96/100" in post
        assert "honeypot" in post
        # The scan deep link always resolves to rugmunch.io/scanner.
        assert "rugmunch.io/scanner" in post
        assert "0xscan1" in post

    def test_scan_card_escapes_user_content(self) -> None:
        post = channels_formatters.format_scan_card(
            {
                "address": "0xscan2",
                "name": "<b>Evil</b>",
                "symbol": "X",
                "chain": "bsc",
                "risk_level": "high",
                "risk_score": 80,
            }
        )
        assert "<b>Evil</b>" not in post
        assert "&lt;b&gt;Evil&lt;/b&gt;" in post

    def test_scan_card_falls_back_to_risk_level_without_score(self) -> None:
        post = channels_formatters.format_scan_card(
            {"address": "0xscan3", "name": "No Score", "symbol": "NS", "chain": "base", "risk_level": "high"}
        )
        assert "Verdict: high" in post

    def test_scan_card_surfaces_real_reasons_not_generic_verdict(self) -> None:
        post = channels_formatters.format_scan_card(
            {
                "address": "0xscan4",
                "name": "Honey Rug",
                "symbol": "HNY",
                "chain": "solana",
                "risk_level": "critical",
                "risk_score": 97,
                "risk_flags": ["honeypot", "bundled_launch", "volume_fake"],
            }
        )
        # The REAL why is surfaced — the generic verdict line is gone.
        assert "sells can be blocked" in post
        assert "bundled launch: supply concentrated" in post
        assert "fake volume: wash-traded" in post
        assert "high-risk verdict from the scanner" not in post

    def test_scan_card_lp_unlocked_from_security_dict(self) -> None:
        post = channels_formatters.format_scan_card(
            {
                "address": "0xscan5",
                "name": "Open LP",
                "symbol": "OLP",
                "chain": "bsc",
                "risk_level": "high",
                "risk_score": 82,
                "risk_flags": ["honeypot"],
                "security": {"lp_locked": False},
            }
        )
        assert "LP unlocked: can be yanked anytime" in post
        assert "honeypot: sells can be blocked" in post

    def test_scan_card_keeps_unknown_flags_raw(self) -> None:
        post = channels_formatters.format_scan_card(
            {
                "address": "0xscan6",
                "name": "Odd",
                "symbol": "ODD",
                "chain": "base",
                "risk_level": "high",
                "risk_score": 75,
                "risk_flags": ["whale_dump_loop"],
            }
        )
        assert "whale_dump_loop" in post

    def test_scan_card_never_renders_unknown_question_marks(self) -> None:
        post = channels_formatters.format_scan_card(
            {
                "address": "0xghost",
                "name": "Unknown",
                "symbol": "???",
                "chain": "ethereum",
                "risk_level": "high",
                "risk_score": 88,
                "risk_flags": ["lp_removed"],
            }
        )
        assert "Unknown (???)" not in post
        assert "(???)" not in post
        assert "Unnamed token" in post

    def test_scan_card_renders_symbol_only_without_question_marks(self) -> None:
        post = channels_formatters.format_scan_card(
            {
                "address": "0xsym",
                "name": "",
                "symbol": "SYM",
                "chain": "bsc",
                "risk_level": "medium",
                "risk_score": 71,
                "risk_flags": ["volume_fake"],
            }
        )
        assert "(???)" not in post
        assert "<b>SYM</b>" in post

    def test_scan_card_renders_name_only_without_question_marks(self) -> None:
        post = channels_formatters.format_scan_card(
            {
                "address": "0xn",
                "name": "Name Only",
                "symbol": "???",
                "chain": "solana",
                "risk_level": "high",
                "risk_score": 90,
                "risk_flags": [],
            }
        )
        assert "(???)" not in post
        assert "Name Only" in post

    def test_scan_card_renders_commentary_when_present_and_omits_when_absent(self) -> None:
        with_line = channels_formatters.format_scan_card(
            {
                "address": "0xc",
                "name": "Witty",
                "symbol": "WIT",
                "chain": "solana",
                "risk_level": "critical",
                "risk_score": 99,
                "risk_flags": ["honeypot"],
                "commentary": "another dog-meme rug — only the exit liquidity is decentralized",
            }
        )
        assert "another dog-meme rug" in with_line
        no_line = channels_formatters.format_scan_card(
            {
                "address": "0xc2",
                "name": "Plain",
                "symbol": "PLN",
                "chain": "solana",
                "risk_level": "critical",
                "risk_score": 99,
                "risk_flags": ["honeypot"],
            }
        )
        assert "another dog-meme rug" not in no_line

    def test_scan_card_escapes_commentary(self) -> None:
        post = channels_formatters.format_scan_card(
            {
                "address": "0xc3",
                "name": "Evil",
                "symbol": "EVL",
                "chain": "solana",
                "risk_level": "critical",
                "risk_score": 99,
                "risk_flags": ["honeypot"],
                "commentary": "<script>alert(1)</script>",
            }
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
            patch.object(channels_content, "_fetch_whale_moves", new=AsyncMock(return_value=[])),
            patch.object(channels_content, "_fetch_early_entries", new=AsyncMock(return_value=[])),
            patch.object(
                channels_content,
                "_fetch_smart_money",
                new=AsyncMock(return_value=[{"address": "w1", "label": "Whale"}]),
            ),
            patch.object(
                channels_content,
                "backend_get_json",
                new=AsyncMock(return_value={"launches": [{"address": "0xL", "name": "NewCoin", "symbol": "NEW"}]}),
            ),
        ):
            content = await channels_content.fetch_alpha_content()
        assert set(content) == {"whale_moves", "smart_money", "launches"}
        assert content["whale_moves"] == []
        assert content["smart_money"][0]["address"] == "w1"
        assert content["launches"][0]["address"] == "0xL"

    async def test_fetch_alpha_content_merges_early_entries_into_whale_moves(self) -> None:
        with (
            patch.object(
                channels_content,
                "_fetch_whale_moves",
                new=AsyncMock(
                    return_value=[
                        {"kind": "whale_move", "token_address": "w", "title": "Whale move", "chain": "solana"}
                    ]
                ),
            ),
            patch.object(
                channels_content,
                "_fetch_early_entries",
                new=AsyncMock(
                    return_value=[
                        {"kind": "early_entry", "token_address": "e", "title": "🐣 Early whale", "chain": "solana"}
                    ]
                ),
            ),
            patch.object(channels_content, "_fetch_smart_money", new=AsyncMock(return_value=[])),
            patch.object(channels_content, "backend_get_json", new=AsyncMock(return_value=None)),
        ):
            content = await channels_content.fetch_alpha_content()
        # Early entries ride the same whale rail (scheduler untouched): both
        # kinds are present, early entries first.
        assert len(content["whale_moves"]) == 2
        assert content["whale_moves"][0]["kind"] == "early_entry"
        assert content["whale_moves"][1]["kind"] == "whale_move"

    async def test_fetch_risk_alerts_merges_security_flags(self) -> None:
        with (
            patch.object(channels_content, "_fetch_pushed_webhook_alerts", new=AsyncMock(return_value=[])),
            patch.object(
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

    async def test_fetch_risk_alerts_merges_webhook_events(self) -> None:
        """Webhook-trigger events (Redis rmi:alerts:recent) reach the digest."""
        webhook_entries = [
            {
                "id": "alt_1755_ab12",
                "type": "helius_webhook",
                "title": "Deployer dump: Binance (cex_hot_wallet) moved 1,200,000 USDC",
                "description": "Creator/team wallet moved 1,200,000 USDC — potential exit pressure.",
                "severity": "high",
                "chain": "solana",
                "token": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                "token_symbol": "USDC",
                "wallet": "0xBeef",
                "risk_score": 85,
                "event_type": "deployer_dump",
                "importance": 0.85,
                "attribution": {
                    "from": "binance (cex_hot_wallet)",
                    "to": "0x1234…abcd",
                },
            }
        ]
        with (
            patch.object(
                channels_content,
                "_fetch_pushed_webhook_alerts",
                new=AsyncMock(return_value=[channels_content._webhook_item(webhook_entries[0])]),
            ),
            patch.object(channels_content, "backend_get_json", new=AsyncMock(return_value=None)),
        ):
            alerts = await channels_content.fetch_risk_alerts()
        assert len(alerts) == 1
        item = alerts[0]
        assert item["alert_type"] == "deployer dump"
        assert item["address"] == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        assert item["chain"] == "solana"
        # Entity labels rendered in the condition line: binance → short address.
        assert "binance (cex_hot_wallet) → 0x1234…abcd" in item["condition"]
        # One-line why-it-matters: the verdict flag needle maps to a known line.
        assert "high_risk_deployer" in item["risk_flags"]
        # Dedupe key is per-alert (stable across restarts).
        assert item["url"] == "alt_1755_ab12"

    async def test_fetch_risk_alerts_interleaves_when_both_rails_have_items(self) -> None:
        scanner = [{"address": "0xsc1", "name": "S1", "symbol": "S1", "risk_level": "high", "risk_score": 80}]
        webhook = [
            channels_content._webhook_item(
                {
                    "id": "alt_1",
                    "type": "helius_webhook",
                    "title": "LP change on MEME",
                    "severity": "medium",
                    "chain": "solana",
                    "token_symbol": "MEME",
                    "event_type": "lp_change",
                }
            )
        ]
        with (
            patch.object(
                channels_content,
                "_fetch_pushed_webhook_alerts",
                new=AsyncMock(return_value=webhook),
            ),
            patch.object(
                channels_content,
                "backend_get_json",
                new=AsyncMock(return_value={"alerts": scanner}),
            ),
        ):
            alerts = await channels_content.fetch_risk_alerts(limit=2)
        assert [a["alert_type"] for a in alerts] == ["scanner", "lp change"]
        assert channels_formatters.alert_verdict(alerts[1]) == "liquidity removed — holders cannot exit"

    async def test_fetch_risk_alerts_webhook_fail_open(self) -> None:
        """Redis down must not break the scanner digest."""
        with (
            patch.object(channels_content, "_fetch_pushed_webhook_alerts", new=AsyncMock(return_value=[])),
            patch.object(
                channels_content,
                "backend_get_json",
                new=AsyncMock(return_value={"alerts": [{"address": "0xok", "symbol": "OK"}]}),
            ),
        ):
            alerts = await channels_content.fetch_risk_alerts()
        assert len(alerts) == 1
        assert alerts[0]["address"] == "0xok"

    async def test_webhook_item_filters_non_webhook_rows(self) -> None:
        # Not a webhook-typed entry → None.
        assert channels_content._webhook_item({"type": "new_pair_risk", "title": "T"}) is None
        # Any *_webhook entry normalizes (the event-type gate lives upstream
        # in _fetch_pushed_webhook_alerts, which skips unknown/low rows).
        item = channels_content._webhook_item(
            {"type": "helius_webhook", "event_type": "whale_move", "severity": "high"}
        )
        assert item is not None
        assert item["alert_type"] == "whale move"

    async def test_fetch_pushed_webhook_alerts_gates_event_types(self) -> None:
        from app.alert_pipeline import get_recent_alerts as _real_get_recent_alerts  # noqa: F401 (patch target)

        entries = [
            {"id": "a1", "type": "helius_webhook", "event_type": "deployer_dump", "severity": "high"},
            {"id": "a2", "type": "helius_webhook", "event_type": "whale_move", "severity": "high"},
            {"id": "a3", "type": "helius_webhook", "event_type": "unknown", "severity": "low"},
            {"id": "a4", "type": "new_pair_risk", "event_type": "deployer_dump", "severity": "high"},
            {"id": "a5", "type": "helius_webhook", "event_type": "", "severity": "critical"},
        ]
        with patch(
            "app.alert_pipeline.get_recent_alerts",
            new=AsyncMock(return_value=entries),
        ):
            items = await channels_content._fetch_pushed_webhook_alerts(limit=10)
        # unknown/low dropped; non-webhook types dropped; missing event_type
        # kept only when severity is high/critical.
        assert [i["url"] for i in items] == ["a1", "a2", "a5"]

    async def test_fetch_pushed_webhook_alerts_fail_open_on_redis_error(self) -> None:
        with patch(
            "app.alert_pipeline.get_recent_alerts",
            new=AsyncMock(side_effect=ConnectionError("redis down")),
        ):
            assert await channels_content._fetch_pushed_webhook_alerts(limit=5) == []


class TestWebhookToAlertsChannelSmoke:
    """End-to-end smoke: a real router-classified webhook event reaches the
    alerts channel digest, rendered with entity labels + why-it-matters."""

    @pytest.mark.asyncio
    async def test_helius_deployer_dump_renders_on_alerts_channel(self) -> None:
        from app.databus.alert_router import route_webhook_alert

        payload = {
            "feePayer": "0xBE0eB53FC46b790099138e3d32C721856d41e865",
            "valueUsd": 1_200_000.0,
            "description": "Deployer wallet transferred 1,200,000 USDC to another wallet",
            "tokenTransfers": [
                {
                    "fromUserAccount": "0xBE0eB53FC46b790099138e3d32C721856d41e865",
                    "toUserAccount": "0xdead0000000000000000000000000000000000",
                    "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
                    "symbol": "USDC",
                    "tokenAmount": {"amount": "1200000000", "decimals": 6, "uiAmount": 1200.0},
                }
            ],
            "signature": "smoke_sig_0001",
            "timestamp": 1755400002,
            "type": "TRANSFERCHECKED",
        }
        captured: dict[str, Any] = {}

        async def _capture_push(**kwargs: Any) -> str:
            captured.update(kwargs)
            return "alt_smoke"

        with (
            patch("app.databus.alert_router._throttle_wallet", return_value="ok"),
            patch("app.alert_pipeline.push_alert", new=_capture_push),
        ):
            result = await route_webhook_alert("helius", payload, "evt:smoke:1")

        assert result["decision"] in ("news", "main")
        # Reconstruct the entry exactly as push_alert stores it in Redis
        # (rmi:alerts:recent), then read it back through the channel fetcher.
        entry: dict[str, Any] = {
            "id": "alt_smoke",
            "type": captured["alert_type"],
            "title": captured["title"],
            "description": captured["description"],
            "severity": captured["severity"],
            "chain": captured["chain"],
            "token": captured["token"],
            "token_symbol": captured["token_symbol"],
            "wallet": captured["wallet"],
            "risk_score": captured["risk_score"],
            **captured["metadata"],
        }
        item = channels_content._webhook_item(entry)
        assert item is not None
        assert item["alert_type"] == result["event_type"].replace("_", " ")

        with (
            patch.object(
                channels_content,
                "_fetch_pushed_webhook_alerts",
                new=AsyncMock(return_value=[item]),
            ),
            patch.object(channels_content, "backend_get_json", new=AsyncMock(return_value=None)),
        ):
            alerts = await channels_content.fetch_risk_alerts(limit=5)

        assert len(alerts) == 1
        text = channels_formatters.format_alerts_post(alerts)
        # Entity label renders (binance → short address), not a bare full address.
        assert "binance (cex_hot_wallet)" in text
        assert "…" in text
        # Clear one-line why it matters.
        assert "Why it matters" in text
        assert "high-risk deployer" in text
        # The token is actionable: scan + chart deep links.
        assert "rugmunch.io/scanner" in text
        assert "gmgn.ai" in text

    async def test_fetch_scan_notes_merges_rails_and_dedupes_by_address(self) -> None:
        with patch.object(
            channels_content,
            "backend_get_json",
            new=AsyncMock(
                side_effect=[
                    # 1st call — /launches/alerts (launch-alert rail).
                    {
                        "alerts": [
                            {
                                "address": "0xdup",
                                "name": "Dup Token",
                                "symbol": "DUP",
                                "chain": "bsc",
                                "risk_level": "critical",
                                "risk_score": 95,
                                "risk_flags": ["honeypot"],
                            }
                        ]
                    },
                    # 2nd call — /data/scan-history (scan_results rail).
                    {
                        "scans": [
                            {
                                "contract_address": "0xdup",
                                "token_name": "Dup Token",
                                "token_symbol": "DUP",
                                "chain": "bsc",
                                "risk_score": 95,
                                "risk_level": "critical",
                            },
                            {
                                "contract_address": "0xsafe",
                                "token_name": "Safe Coin",
                                "token_symbol": "SAFE",
                                "chain": "solana",
                                "risk_score": 12,
                                "risk_level": "low",
                            },
                            {"token_symbol": "NOADDR", "risk_score": 50},
                        ]
                    },
                ]
            ),
        ):
            notes = await channels_content.fetch_scan_notes(limit=5)
        # 0xdup appears once (launch-alert variant wins), 0xsafe included,
        # the address-less record is dropped; sorted by risk score descending.
        assert len(notes) == 2
        by_addr = {n["address"].lower(): n for n in notes}
        assert "0xdup" in by_addr
        assert by_addr["0xdup"]["risk_flags"] == ["honeypot"]
        assert by_addr["0xdup"]["condition"] == "risk 95/100 ≥ 70 (critical)"
        assert "0xsafe" in by_addr
        assert by_addr["0xsafe"]["condition"] == "risk 12/100 (low)"
        assert notes[0]["risk_score"] == 95
        assert notes[1]["risk_score"] == 12

    async def test_fetch_scan_notes_derives_flags_from_scan_record_booleans(self) -> None:
        with patch.object(
            channels_content,
            "backend_get_json",
            new=AsyncMock(
                side_effect=[
                    None,  # /launches/alerts miss
                    {
                        "scans": [
                            {
                                "contract_address": "0xrug",
                                "token_name": "Rug Record",
                                "token_symbol": "RGR",
                                "chain": "ethereum",
                                "risk_score": 90,
                                "risk_level": "high",
                                "is_honeypot": False,
                                "is_rug_pull": True,
                                "has_mint_authority": True,
                            }
                        ]
                    },
                ]
            ),
        ):
            notes = await channels_content.fetch_scan_notes(limit=5)
        assert len(notes) == 1
        assert "rug_pull" in notes[0]["risk_flags"]
        assert "mint_authority" in notes[0]["risk_flags"]
        assert "honeypot" not in notes[0]["risk_flags"]

    async def test_fetch_scan_notes_empty_on_backend_miss(self) -> None:
        with patch.object(channels_content, "backend_get_json", new=AsyncMock(return_value=None)):
            assert await channels_content.fetch_scan_notes() == []

    async def test_fetch_scan_notes_enriches_placeholder_identity_from_dexscreener(self) -> None:
        with (
            patch.object(
                channels_content,
                "backend_get_json",
                new=AsyncMock(
                    side_effect=[
                        None,  # /launches/alerts miss
                        {
                            "scans": [
                                {
                                    "contract_address": "0xghost",
                                    "token_name": "",
                                    "token_symbol": "",
                                    "chain": "solana",
                                    "risk_score": 90,
                                    "risk_level": "high",
                                    "is_honeypot": True,
                                }
                            ]
                        },
                    ]
                ),
            ),
            patch.object(
                channels_content,
                "http_get_json",
                new=AsyncMock(
                    return_value={
                        "pairs": [{"chainId": "solana", "baseToken": {"name": "Ghost Coin", "symbol": "GHOST"}}]
                    }
                ),
            ),
        ):
            notes = await channels_content.fetch_scan_notes(limit=5)
        assert len(notes) == 1
        assert notes[0]["name"] == "Ghost Coin"
        assert notes[0]["symbol"] == "GHOST"

    async def test_fetch_scan_notes_derives_liquidity_removed_flag(self) -> None:
        with patch.object(
            channels_content,
            "backend_get_json",
            new=AsyncMock(
                side_effect=[
                    None,
                    {
                        "scans": [
                            {
                                "contract_address": "0xopenlp",
                                "token_name": "Open LP",
                                "token_symbol": "OLP",
                                "chain": "ethereum",
                                "risk_score": 85,
                                "risk_level": "high",
                                "liquidity_locked": False,
                            }
                        ]
                    },
                ]
            ),
        ):
            notes = await channels_content.fetch_scan_notes(limit=5)
        assert len(notes) == 1
        assert "liquidity_removed" in notes[0]["risk_flags"]

    async def test_fetch_scan_notes_does_not_overwrite_real_identity(self) -> None:
        with (
            patch.object(
                channels_content,
                "backend_get_json",
                new=AsyncMock(
                    side_effect=[
                        None,
                        {
                            "scans": [
                                {
                                    "contract_address": "0xreal",
                                    "token_name": "Real Token",
                                    "token_symbol": "REAL",
                                    "chain": "solana",
                                    "risk_score": 90,
                                    "risk_level": "high",
                                }
                            ]
                        },
                    ]
                ),
            ),
            patch.object(channels_content, "http_get_json", new=AsyncMock()) as dex_mock,
        ):
            notes = await channels_content.fetch_scan_notes(limit=5)
        assert notes[0]["name"] == "Real Token"
        assert notes[0]["symbol"] == "REAL"
        dex_mock.assert_not_awaited()


# ── Scan commentary (witty one-liner helper) ──────────────────────────────


class TestScanCommentary:
    async def test_attaches_honest_witty_line(self) -> None:
        item = {
            "address": "0xw",
            "name": "Dog Wif Rug",
            "symbol": "DWR",
            "chain": "solana",
            "risk_level": "critical",
            "risk_score": 96,
            "risk_flags": ["honeypot", "bundled_launch"],
        }
        with patch(
            "app.llm_config.chat_with_fallback",
            new=AsyncMock(return_value="the only thing decentralized here is the exit liquidity"),
        ):
            out = await channels_scan_commentary.attach_scan_commentary(item)
        assert out is item
        assert item["commentary"] == "the only thing decentralized here is the exit liquidity"

    async def test_prompt_is_grounded_in_real_flags(self) -> None:
        item = {
            "address": "0xw2",
            "name": "Honey",
            "symbol": "HNY",
            "chain": "bsc",
            "risk_level": "high",
            "risk_score": 90,
            "risk_flags": ["honeypot", "lp_removed"],
        }
        with patch("app.llm_config.chat_with_fallback", new=AsyncMock(return_value="ok")) as llm:
            await channels_scan_commentary.attach_scan_commentary(item)
        prompt = llm.await_args.args[0][1]["content"]
        assert "honeypot" in prompt
        assert "lp_removed" in prompt
        assert "Honey (HNY)" in prompt

    async def test_fails_open_to_no_line(self) -> None:
        item = {
            "address": "0xw3",
            "name": "Boom",
            "symbol": "BOOM",
            "chain": "solana",
            "risk_level": "high",
            "risk_score": 90,
            "risk_flags": ["honeypot"],
        }
        with patch("app.llm_config.chat_with_fallback", new=AsyncMock(side_effect=RuntimeError("provider down"))):
            out = await channels_scan_commentary.attach_scan_commentary(item)
        assert out is item
        assert "commentary" not in item

    async def test_skips_clean_tokens_without_llm_call(self) -> None:
        item = {
            "address": "0xw4",
            "name": "Clean",
            "symbol": "CLN",
            "chain": "base",
            "risk_level": "low",
            "risk_score": 10,
            "risk_flags": [],
        }
        with patch("app.llm_config.chat_with_fallback", new=AsyncMock()) as llm:
            out = await channels_scan_commentary.attach_scan_commentary(item)
        assert out is item
        assert "commentary" not in item
        llm.assert_not_awaited()

    async def test_clips_degenerate_output(self) -> None:
        item = {
            "address": "0xw5",
            "name": "Long",
            "symbol": "LNG",
            "chain": "solana",
            "risk_level": "high",
            "risk_score": 90,
            "risk_flags": ["honeypot"],
        }
        long_line = "x" * 500
        with patch("app.llm_config.chat_with_fallback", new=AsyncMock(return_value=long_line)):
            await channels_scan_commentary.attach_scan_commentary(item)
        assert len(item["commentary"]) <= channels_scan_commentary._COMMENTARY_MAX_CHARS


# ── Scheduler ─────────────────────────────────────────────────────────────


class FakeJobQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    def run_repeating(self, callback: Any, interval: Any, first: Any = None, name: Any = None, **kwargs: Any) -> None:
        self.jobs.append({"callback": callback, "interval": interval, "first": first, "name": name})

    def run_daily(self, callback: Any, time: Any, days: Any = None, name: Any = None, **kwargs: Any) -> None:
        self.jobs.append({"callback": callback, "time": time, "days": days, "name": name})


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

    async def test_scan_job_posts_one_compact_card_per_notable_scan(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        _stub_scorer(monkeypatch, fallback=False, scores=[0.55, 0.45])
        items = [
            {
                "address": "0xnote1",
                "name": "Note Token",
                "symbol": "NOTE",
                "chain": "ethereum",
                "risk_level": "high",
                "risk_score": 88,
                "risk_flags": ["rug_pull"],
            },
            {
                "address": "0xnote2",
                "name": "Other Token",
                "symbol": "OTH",
                "chain": "solana",
                "risk_level": "medium",
                "risk_score": 71,
                "risk_flags": ["low_liquidity"],
            },
        ]
        with (
            patch.object(channels_scheduler, "fetch_scan_notes", new=AsyncMock(return_value=items)),
            patch.object(channels_scheduler, "attach_scan_commentary", new=AsyncMock(side_effect=lambda i: i)),
        ):
            assert await channels_scheduler.post_scan_update(ctx) == 2
        assert bot.send_message.await_count == 2
        texts = [call.kwargs["text"] for call in bot.send_message.await_args_list]
        assert all("Scan of note" in t for t in texts)
        assert any("NOTE" in t for t in texts)
        assert any("rugmunch.io/scanner" in t for t in texts)

    async def test_scan_job_gates_below_floor_in_rerank_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        _stub_scorer(monkeypatch, fallback=False, scores=[0.1])
        item = {"address": "0xlow", "name": "Noise", "symbol": "NOI", "risk_level": "low", "risk_score": 30}
        with patch.object(channels_scheduler, "fetch_scan_notes", new=AsyncMock(return_value=[item])):
            assert await channels_scheduler.post_scan_update(ctx) == 0
        bot.send_message.assert_not_awaited()

    async def test_scan_job_dedupes_token_across_ticks(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        _stub_scorer(monkeypatch, fallback=True)
        item = {
            "address": "0xsame",
            "name": "Same Token",
            "symbol": "SAME",
            "chain": "solana",
            "risk_level": "high",
            "risk_score": 90,
        }
        with (
            patch.object(channels_scheduler, "fetch_scan_notes", new=AsyncMock(return_value=[item])),
            patch.object(channels_scheduler, "attach_scan_commentary", new=AsyncMock(side_effect=lambda i: i)),
        ):
            assert await channels_scheduler.post_scan_update(ctx) == 1
            assert await channels_scheduler.post_scan_update(ctx) == 0  # same token never reposted
        assert bot.send_message.await_count == 1

    async def test_scan_job_fails_open_when_no_scans(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        with patch.object(channels_scheduler, "fetch_scan_notes", new=AsyncMock(return_value=[])):
            assert await channels_scheduler.post_scan_update(ctx) == 0
        bot.send_message.assert_not_awaited()

    async def test_scan_job_skips_when_channel_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ctx, bot = _ctx()
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: None)
        with patch.object(channels_scheduler, "fetch_scan_notes", new=AsyncMock(return_value=[])):
            assert await channels_scheduler.post_scan_update(ctx) == 0
        bot.send_message.assert_not_awaited()

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
        monkeypatch.setattr(channels_scheduler, "SCAN_CHANNEL_ENABLED", True)
        monkeypatch.setattr(channels_scheduler, "_chat_id", lambda channel: f"-100{channel}")
        fake_jq = FakeJobQueue()
        app = SimpleNamespace(job_queue=fake_jq)
        channels_scheduler.register_channel_jobs(app)  # type: ignore[arg-type]
        # The legacy ``channel_news`` job is deprecated (commented out); the
        # routed tg_news lane now owns the news channel. Every enabled job
        # with a resolvable chat id registers (scam_watch needs no chat id);
        # the daily market summary registers when the main chat id resolves.
        assert {j["name"] for j in fake_jq.jobs} == {
            "channel_alerts",
            "channel_alpha",
            "channel_tg_news",
            "channel_scam_watch",
            "channel_scans",
            "daily_market_summary",
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
