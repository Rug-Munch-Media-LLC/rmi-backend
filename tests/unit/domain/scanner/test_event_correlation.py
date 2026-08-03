"""Tests for event_correlation — news-event-to-price correlation engine.

Covers:
  - score_correlation classification (all branches)
  - _normalize_sentiment aliasing
  - _token_lookup_keys (address vs ticker)
  - _match_news_item keyword matching
  - _price_change_pct math
  - correlate_news_with_price happy path + empty news path

All external dependencies (Postgres, ClickHouse, RSS/API feeds) are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.event_correlation import (
    _match_news_item,
    _normalize_sentiment,
    _price_change_pct,
    _token_lookup_keys,
    correlate_news_with_price,
    score_correlation,
)

_TOKEN = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
_CHAIN = "ethereum"

# ── Mock news articles (one bullish, one bearish, one neutral) ──
_MOCK_NEWS = [
    {
        "news_id": "n1",
        "title": "Coldcard exploit sparks Bitcoin flight",
        "url": "https://example.com/1",
        "summary": "Investors dump BTC after security incident",
        "source": "TestSource",
        "published_at": "2026-08-03T10:00:00+00:00",
        "sentiment_score": -0.8,
        "tokens_mentioned": ["$BTC"],
    },
    {
        "news_id": "n2",
        "title": "Ethereum upgrade boosts confidence",
        "url": "https://example.com/2",
        "summary": "ETH rally as network improves",
        "source": "TestSource",
        "published_at": "2026-08-03T12:00:00+00:00",
        "sentiment_score": 0.8,
        "tokens_mentioned": ["$ETH"],
    },
    {
        "news_id": "n3",
        "title": "Market update roundup",
        "url": "https://example.com/3",
        "summary": "Flat trading day across majors",
        "source": "TestSource",
        "published_at": "2026-08-03T14:00:00+00:00",
        "sentiment_score": 0.0,
        "tokens_mentioned": ["$BTC"],
    },
]


class TestScoreCorrelation:
    """Tests for score_correlation — the classification function."""

    @pytest.mark.parametrize(
        ("sentiment", "pct", "expected"),
        [
            # Below threshold -> NO_CORRELATION regardless of direction
            ("BULLISH", 0.2, "NO_CORRELATION"),
            ("BEARISH", -0.4, "NO_CORRELATION"),
            # Zero move
            ("BULLISH", 0.0, "NO_CORRELATION"),
            # Strong aligned
            ("BULLISH", 6.0, "STRONG_CORRELATION"),
            ("BEARISH", -6.0, "STRONG_CORRELATION"),
            # Moderate aligned
            ("BULLISH", 3.0, "MODERATE_CORRELATION"),
            ("BEARISH", -3.0, "MODERATE_CORRELATION"),
            # Aligned but weak -> below threshold still NO
            ("BULLISH", 0.7, "NO_CORRELATION"),
            # Mismatched direction -> NO
            ("BULLISH", -3.0, "NO_CORRELATION"),
            ("BEARISH", 3.0, "NO_CORRELATION"),
            # Neutral sentiment -> never correlates
            ("NEUTRAL", 6.0, "NO_CORRELATION"),
            ("NEUTRAL", -6.0, "NO_CORRELATION"),
        ],
    )
    def test_classification(self, sentiment: str, pct: float, expected: str) -> None:
        assert score_correlation(sentiment, pct) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("bullish", "BULLISH"),
            ("slightly_bullish", "BULLISH"),
            ("BEARISH", "BEARISH"),
            ("slightly_bearish", "BEARISH"),
            ("neutral", "NEUTRAL"),
            ("", "NEUTRAL"),
            (None, "NEUTRAL"),
            ("garbage", "NEUTRAL"),
        ],
    )
    def test_normalize_sentiment(self, raw: str | None, expected: str) -> None:
        assert _normalize_sentiment(raw or "") == expected


class TestTokenLookupKeys:
    """Tests for _token_lookup_keys."""

    def test_evm_address_gets_hex_forms(self) -> None:
        keys = _token_lookup_keys(_TOKEN, _CHAIN)
        assert _TOKEN.lower() in keys
        assert _TOKEN.lower()[2:12] in keys
        # native alias for ethereum chain
        assert "eth" in keys

    def test_bare_ticker(self) -> None:
        keys = _token_lookup_keys("BTC", "ethereum")
        assert "btc" in keys
        assert "$btc" in keys

    def test_solana_address_preserved(self) -> None:
        addr = "So11111111111111111111111111111111111111112"
        keys = _token_lookup_keys(addr, "solana")
        assert addr.lower() in keys
        assert "sol" in keys


class TestMatchNewsItem:
    """Tests for _match_news_item."""

    def test_matches_ticker_in_title(self) -> None:
        assert _match_news_item(_MOCK_NEWS[0], ["btc", "$btc"])

    def test_matches_ticker_in_summary(self) -> None:
        item = {"title": "No ticker here", "summary": "BTC price dumps hard", "tokens_mentioned": []}
        assert _match_news_item(item, ["btc"])

    def test_matches_tokens_mentioned(self) -> None:
        item = {"title": "x", "summary": "", "tokens_mentioned": ["$ETH"]}
        assert _match_news_item(item, ["eth", "$eth"])

    def test_no_match(self) -> None:
        item = {"title": "Unrelated story", "summary": "Nothing here", "tokens_mentioned": ["$DOGE"]}
        assert not _match_news_item(item, ["btc"])


class TestPriceChangePct:
    """Tests for _price_change_pct."""

    def test_normal_change(self) -> None:
        assert _price_change_pct(100.0, 95.0) == -5.0

    def test_positive_change(self) -> None:
        assert _price_change_pct(100.0, 110.0) == 10.0

    def test_zero_before_returns_zero(self) -> None:
        assert _price_change_pct(0.0, 110.0) == 0.0


class TestCorrelateNewsWithPrice:
    """Tests for correlate_news_with_price."""

    @pytest.mark.asyncio
    async def test_returns_events_with_correlation(self) -> None:
        """Happy path: news events are priced and classified."""
        price_before = {"price_usd": 100.0, "source": "own_dex_events_at_time"}
        price_after_down = {"price_usd": 94.0, "source": "own_dex_events_at_time"}
        price_after_up = {"price_usd": 106.0, "source": "own_dex_events_at_time"}

        with (
            patch(
                "app.domains.scanner.engines.event_correlation.fetch_token_news",
                AsyncMock(return_value=_MOCK_NEWS),
            ),
            patch(
                "app.domains.scanner.engines.event_correlation._sample_price_at",
                AsyncMock(
                    side_effect=[
                        price_before,
                        price_after_down,
                        price_before,
                        price_after_up,
                        price_before,
                        price_before,
                    ]
                ),
            ),
        ):
            result = await correlate_news_with_price(_TOKEN, _CHAIN, lookback_hours=24)

        assert result["events"], "expected at least one event"
        # Event 1: bearish sentiment, price dropped -> STRONG_CORRELATION
        e1 = result["events"][0]
        assert e1["sentiment"] == "BEARISH"
        assert e1["price_change_pct"] == -6.0
        assert e1["correlation"] == "STRONG_CORRELATION"
        # Event 2: bullish sentiment, price rose -> STRONG_CORRELATION
        e2 = result["events"][1]
        assert e2["sentiment"] == "BULLISH"
        assert e2["price_change_pct"] == 6.0
        assert e2["correlation"] == "STRONG_CORRELATION"
        assert "correlated with price movements" in result["summary"]

    @pytest.mark.asyncio
    async def test_no_news_returns_empty(self) -> None:
        """No news -> empty events and a clear summary."""
        with patch(
            "app.domains.scanner.engines.event_correlation.fetch_token_news",
            AsyncMock(return_value=[]),
        ):
            result = await correlate_news_with_price(_TOKEN, _CHAIN, lookback_hours=24)

        assert result["events"] == []
        assert "No news events found" in result["summary"]

    @pytest.mark.asyncio
    async def test_unpriced_news_is_skipped(self) -> None:
        """News with no price data must not break the loop."""
        news = [_MOCK_NEWS[0]]
        with (
            patch(
                "app.domains.scanner.engines.event_correlation.fetch_token_news",
                AsyncMock(return_value=news),
            ),
            patch(
                "app.domains.scanner.engines.event_correlation._sample_price_at",
                AsyncMock(return_value=None),
            ),
        ):
            result = await correlate_news_with_price(_TOKEN, _CHAIN, lookback_hours=24)

        assert result["events"] == []
        assert "No news events found" in result["summary"]

    @pytest.mark.asyncio
    async def test_graceful_on_price_exception(self) -> None:
        """A raising price oracle must not propagate (defensive wrapper)."""
        with (
            patch(
                "app.domains.scanner.engines.event_correlation.fetch_token_news",
                AsyncMock(return_value=[_MOCK_NEWS[0]]),
            ),
            patch(
                "app.domains.scanner.engines.event_correlation.get_token_price_at_time",
                AsyncMock(side_effect=RuntimeError("ch down")),
            ),
        ):
            result = await correlate_news_with_price(_TOKEN, _CHAIN, lookback_hours=24)

        assert result["events"] == []

    def test_published_at_parse_rejects_bad(self) -> None:
        from app.domains.scanner.engines.event_correlation import _parse_published_at

        assert _parse_published_at("") is None
        assert _parse_published_at(None) is None
        assert _parse_published_at("not-a-date") is None
        parsed = _parse_published_at("2026-08-03T10:00:00Z")
        assert parsed is not None
        assert parsed.tzinfo is not None


class TestScannerIntegration:
    """ScanResult model carries event_correlation."""

    def test_scan_result_has_field(self) -> None:
        from app.domains.scanner.unified.model import ScanResult

        r = ScanResult(token_address=_TOKEN, chain=_CHAIN)
        assert r.event_correlation is None
        r.event_correlation = {"events": [], "summary": "none"}
        assert r.event_correlation["summary"] == "none"

    def test_legacy_dict_includes_field(self) -> None:
        from app.domains.scanner.unified.model import ScanResult
        from app.domains.scanner.unified.scanner import _to_legacy_dict

        r = ScanResult(token_address=_TOKEN, chain=_CHAIN)
        r.event_correlation = {"events": [], "summary": "none"}
        out = _to_legacy_dict(r)
        assert out["event_correlation"] == {"events": [], "summary": "none"}


class TestTelegramCommand:
    """cmd_events wiring — command module imports cleanly."""

    def test_cmd_events_importable(self) -> None:
        from app.domains.telegram.rugmunchbot.commands.events import _fmt_change, _fmt_price, cmd_events

        assert callable(cmd_events)
        assert _fmt_price(12345.678) == "$12,345.68"
        assert _fmt_change(-1.12) == "📉 -1.12%"
        assert _fmt_change(2.5) == "📈 +2.50%"
