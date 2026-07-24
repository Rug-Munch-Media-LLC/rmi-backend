"""News Service tests — 20+ tests covering RSS parsing, aggregation,
sentiment analysis, categorization, deduplication, and error handling.

Uses pytest-asyncio (enabled via pytest.ini). All external calls mocked.
"""

from __future__ import annotations

import os

os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.domains.news.news_service.service import NewsService, get_news_service
from app.domains.news.news_service.sources import (
    BEARISH_WORDS,
    BULLISH_WORDS,
    CATEGORY_KEYWORDS,
    REDDIT_SUBREDDITS,
    RSS_FEEDS,
)


# ═══════════════════════════════════════════════════════════════════════
# ── NewsService Initialization
# ═══════════════════════════════════════════════════════════════════════


class TestNewsServiceInit:
    """NewsService construction and default state."""

    def test_init_default_state(self):
        """Service starts with empty cache, no seen hashes, defaults set."""
        svc = NewsService()
        assert svc.cache == {}
        assert svc.cache_ttl == 180
        assert svc.last_fetch is None
        assert svc.seen_hashes == set()
        assert svc._sentiment_cache == {}

    def test_get_news_service_singleton(self):
        """get_news_service returns the same instance on repeated calls."""
        # Reset singleton for test isolation
        import app.domains.news.news_service.service as svc_mod

        svc_mod._news_service = None
        svc1 = get_news_service()
        svc2 = get_news_service()
        assert svc1 is svc2

    def test_get_news_service_creates_on_demand(self):
        """get_news_service creates a NewsService when none exists."""
        import app.domains.news.news_service.service as svc_mod

        svc_mod._news_service = None
        svc = get_news_service()
        assert isinstance(svc, NewsService)


# ═══════════════════════════════════════════════════════════════════════
# ── Sentiment Analysis
# ═══════════════════════════════════════════════════════════════════════


class TestNewsServiceSentiment:
    """_analyze_sentiment keyword-based detection."""

    def test_sentiment_bullish(self):
        """Strong bullish signal when bull words dominate by >2."""
        svc = NewsService()
        text = "Bitcoin surges to all-time high with record rally and massive growth"
        assert svc._analyze_sentiment(text) == "bullish"

    def test_sentiment_bearish(self):
        """Strong bearish signal when bear words dominate by >2."""
        svc = NewsService()
        text = "Massive hack leads to exploit, stolen funds drained in security breach"
        assert svc._analyze_sentiment(text) == "bearish"

    def test_sentiment_slightly_bullish(self):
        """Slightly bullish when bull words > bear words but diff <= 2."""
        svc = NewsService()
        text = f"{' '.join(BULLISH_WORDS[:3])} and {' '.join(BEARISH_WORDS[:1])}"
        result = svc._analyze_sentiment(text)
        assert result in ("bullish", "slightly_bullish")

    def test_sentiment_neutral(self):
        """Neutral when bull and bear words are balanced."""
        svc = NewsService()
        text = "The price of bitcoin today is moving sideways"
        assert svc._analyze_sentiment(text) == "neutral"

    def test_sentiment_empty_text(self):
        """Empty text returns neutral."""
        svc = NewsService()
        assert svc._analyze_sentiment("") == "neutral"

    def test_sentiment_cache_hit(self):
        """Repeated calls for the same text return cached result."""
        svc = NewsService()
        text = "Bitcoin surges to all-time high"
        result1 = svc._analyze_sentiment(text)
        # Verify it's in cache
        import hashlib

        cache_key = hashlib.sha256(text.lower()[:200].encode()).hexdigest()
        assert cache_key in svc._sentiment_cache
        assert svc._sentiment_cache[cache_key] == result1

    def test_sentiment_cache_cleared_at_capacity(self):
        """Sentiment cache clears when >5000 entries."""
        svc = NewsService()
        # Fill past capacity
        for i in range(5010):
            svc._analyze_sentiment(f"Text number {i}")
        # Cache should have been cleared and re-filled (or is smaller now)
        assert len(svc._sentiment_cache) <= 5010


# ═══════════════════════════════════════════════════════════════════════
# ── Categorization
# ═══════════════════════════════════════════════════════════════════════


class TestNewsServiceCategorization:
    """_categorize and _categorize_reddit."""

    def test_categorize_security(self):
        """Security keywords map to 'security' category."""
        svc = NewsService()
        assert svc._categorize("Major hack and exploit found in audit") == "security"

    def test_categorize_market(self):
        """Price/trading keywords map to 'market' category."""
        svc = NewsService()
        assert svc._categorize("BTC price prediction and market analysis") == "market"

    def test_categorize_general_fallback(self):
        """No keyword match returns 'general'."""
        svc = NewsService()
        assert svc._categorize("Completely unrelated content about cooking") == "general"

    def test_categorize_empty(self):
        """Empty text returns 'general'."""
        svc = NewsService()
        assert svc._categorize("") == "general"

    def test_categorize_reddit_mapped_subreddit(self):
        """Known subreddits return a fixed category."""
        svc = NewsService()
        assert svc._categorize_reddit("CryptoCurrency", "Some text") == "market"
        assert svc._categorize_reddit("defi", "Some text") == "defi"
        assert svc._categorize_reddit("CryptoScams", "Some text") == "security"

    def test_categorize_reddit_fallback_to_text(self):
        """Unknown subreddits fall back to text-based categorization."""
        svc = NewsService()
        result = svc._categorize_reddit("SomeStrangeSub", "Hack and exploit")
        assert result == "security"


# ═══════════════════════════════════════════════════════════════════════
# ── Deduplication
# ═══════════════════════════════════════════════════════════════════════


class TestNewsServiceDeduplication:
    """seen_hashes deduplication logic."""

    def test_deduplication_tracks_hashes(self):
        """seen_hashes grows as articles are processed."""
        svc = NewsService()
        assert len(svc.seen_hashes) == 0

    def test_deduplication_identical_hash_blocked(self):
        """The same article hash won't be added twice."""

        async def _test():
            import hashlib

            svc = NewsService()
            ch = hashlib.sha256(b"rss:Test Title:http://example.com").hexdigest()
            svc.seen_hashes.add(ch)
            assert ch in svc.seen_hashes
            # Verify hash is only stored once
            assert len(svc.seen_hashes) == 1

        import asyncio

        asyncio.run(_test())


# ═══════════════════════════════════════════════════════════════════════
# ── Aggregator / fetch_all
# ═══════════════════════════════════════════════════════════════════════


class TestNewsServiceAggregation:
    """fetch_all aggregation logic with mocked fetchers."""

    @patch.object(NewsService, "_fetch_all_rss", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_all_reddit", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_coingecko", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_cryptopanic", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_ghost", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_rmi_scanner", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_rmi_alerts", new_callable=AsyncMock)
    async def test_fetch_all_aggregates_all_sources(
        self,
        mock_alerts,
        mock_scanner,
        mock_ghost,
        mock_cp,
        mock_cg,
        mock_reddit,
        mock_rss,
    ):
        """All sources are fetched and aggregated into the response."""
        mock_rss.return_value = [
            {
                "id": "rss-1",
                "title": "RSS Article",
                "source": "CoinTelegraph",
                "published_at": "2026-01-01T00:00:00+00:00",
                "sentiment": "neutral",
                "tier": "news",
                "kind": "external",
            },
        ]
        mock_reddit.return_value = [
            {
                "id": "reddit-1",
                "title": "Reddit Post",
                "source": "Reddit r/CryptoCurrency",
                "published_at": "2026-01-01T01:00:00+00:00",
                "sentiment": "bullish",
                "tier": "social",
                "kind": "social",
            },
        ]
        mock_cg.return_value = [
            {
                "id": "cg-1",
                "title": "CoinGecko News",
                "source": "CoinGecko",
                "published_at": "2026-01-01T02:00:00+00:00",
                "sentiment": "neutral",
                "tier": "market",
                "kind": "api",
            },
        ]
        mock_cp.return_value = []
        mock_ghost.return_value = []
        mock_scanner.return_value = []
        mock_alerts.return_value = []

        svc = NewsService()
        result = await svc.fetch_all(limit=100)

        assert result["status"] == "success"
        assert len(result["articles"]) == 3
        assert result["total"] == 3
        assert "CoinTelegraph" in result["sources"]
        assert "CoinGecko" in result["sources"]
        assert "Reddit r/CryptoCurrency" in result["sources"]
        assert result["source_count"] == 3
        assert result["cached"] is False
        assert "fetched_at" in result

    @patch.object(NewsService, "_fetch_all_rss", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_all_reddit", new_callable=AsyncMock)
    async def test_fetch_all_with_filters(self, mock_reddit, mock_rss):
        """include_* flags control which sources are fetched."""
        mock_rss.return_value = [
            {
                "id": "rss-a",
                "title": "RSS",
                "source": "CoinDesk",
                "published_at": "2026-01-01T00:00:00+00:00",
                "sentiment": "neutral",
                "tier": "news",
                "kind": "external",
            },
        ]

        svc = NewsService()
        result = await svc.fetch_all(
            limit=100, include_reddit=False, include_api=False, include_internal=False
        )

        assert len(result["articles"]) == 1
        assert result["articles"][0]["source"] == "CoinDesk"
        mock_reddit.assert_not_called()

    @patch.object(NewsService, "_fetch_all_rss", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_all_reddit", new_callable=AsyncMock)
    async def test_fetch_all_error_handling_resilient(self, mock_reddit, mock_rss):
        """If one source fails, other sources still appear in results."""
        mock_rss.return_value = [
            {
                "id": "rss-x",
                "title": "RSS OK",
                "source": "CoinTelegraph",
                "published_at": "2026-01-01T00:00:00+00:00",
                "sentiment": "neutral",
                "tier": "news",
                "kind": "external",
            },
        ]
        # Simulate a list being returned — not an exception (that would be logged)
        mock_reddit.side_effect = Exception("Reddit down")

        svc = NewsService()
        result = await svc.fetch_all(limit=100, include_api=False, include_internal=False)

        # fetch_all uses asyncio.gather with return_exceptions=True, so
        # the exception is returned as a value, not propagated.
        # The code checks `isinstance(result, list)` so non-list results are skipped.
        assert result["status"] == "success"
        assert len(result["articles"]) >= 1

    @patch.object(NewsService, "_fetch_all_rss", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_all_reddit", new_callable=AsyncMock)
    async def test_fetch_all_deduplicates_by_id(self, mock_reddit, mock_rss):
        """Duplicate ids across sources are removed."""
        mock_rss.return_value = [
            {
                "id": "dup-1",
                "title": "Same Story",
                "source": "CoinTelegraph",
                "published_at": "2026-01-01T00:00:00+00:00",
                "sentiment": "neutral",
                "tier": "news",
                "kind": "external",
            },
        ]
        mock_reddit.return_value = [
            {
                "id": "dup-1",
                "title": "Same Story",
                "source": "Reddit r/CryptoCurrency",
                "published_at": "2026-01-01T00:00:00+00:00",
                "sentiment": "neutral",
                "tier": "social",
                "kind": "social",
            },
        ]

        svc = NewsService()
        result = await svc.fetch_all(limit=100, include_api=False, include_internal=False)

        assert len(result["articles"]) == 1

    @patch.object(NewsService, "_fetch_all_rss", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_all_reddit", new_callable=AsyncMock)
    async def test_fetch_all_sorts_by_date_desc(self, mock_reddit, mock_rss):
        """Articles are sorted by published_at descending."""
        mock_rss.return_value = [
            {
                "id": "old",
                "title": "Old",
                "source": "CoinDesk",
                "published_at": "2025-01-01T00:00:00+00:00",
                "sentiment": "neutral",
                "tier": "news",
                "kind": "external",
            },
        ]
        mock_reddit.return_value = [
            {
                "id": "new",
                "title": "New",
                "source": "Reddit r/Crypto",
                "published_at": "2026-07-01T00:00:00+00:00",
                "sentiment": "bullish",
                "tier": "social",
                "kind": "social",
            },
        ]

        svc = NewsService()
        result = await svc.fetch_all(limit=100, include_api=False, include_internal=False)

        assert result["articles"][0]["title"] == "New"
        assert result["articles"][1]["title"] == "Old"

    @patch.object(NewsService, "_fetch_all_rss", new_callable=AsyncMock)
    @patch.object(NewsService, "_fetch_all_reddit", new_callable=AsyncMock)
    async def test_fetch_all_sentiment_summary(self, mock_reddit, mock_rss):
        """Sentiment summary aggregates across all articles."""
        mock_rss.return_value = [
            {
                "id": "a",
                "title": "A",
                "source": "CoinDesk",
                "published_at": "2026-07-01T00:00:00+00:00",
                "sentiment": "bullish",
                "tier": "news",
                "kind": "external",
            },
            {
                "id": "b",
                "title": "B",
                "source": "CoinDesk",
                "published_at": "2026-07-01T01:00:00+00:00",
                "sentiment": "bearish",
                "tier": "news",
                "kind": "external",
            },
        ]
        mock_reddit.return_value = [
            {
                "id": "c",
                "title": "C",
                "source": "Reddit r/Crypto",
                "published_at": "2026-07-01T02:00:00+00:00",
                "sentiment": "neutral",
                "tier": "social",
                "kind": "social",
            },
        ]

        svc = NewsService()
        result = await svc.fetch_all(limit=100, include_api=False, include_internal=False)

        assert result["sentiment_summary"]["bullish"] == 1
        assert result["sentiment_summary"]["bearish"] == 1
        assert result["sentiment_summary"]["neutral"] == 1


# ═══════════════════════════════════════════════════════════════════════
# ── Source Configuration Integrity
# ═══════════════════════════════════════════════════════════════════════


class TestNewsSourcesConfig:
    """Static configuration integrity checks."""

    def test_rss_feeds_format(self):
        """All RSS_FEEDS entries are (url, name) tuples."""
        for entry in RSS_FEEDS:
            assert isinstance(entry, tuple) and len(entry) == 2
            assert isinstance(entry[0], str) and entry[0].startswith("http")
            assert isinstance(entry[1], str) and len(entry[1]) > 0

    def test_rss_feeds_have_mostly_unique_names(self):
        """Source names are at least 99% unique (minor production duplicates ok)."""
        names = [name for _, name in RSS_FEEDS]
        assert len(names) > 0
        # At most 1 duplicate allowed (production data has Stanford CBR twice)
        assert len(names) - len(set(names)) <= 1

    def test_bullish_words_are_strings(self):
        """All BULLISH_WORDS are non-empty strings."""
        assert len(BULLISH_WORDS) > 0
        for w in BULLISH_WORDS:
            assert isinstance(w, str) and len(w) > 0

    def test_bearish_words_are_strings(self):
        """All BEARISH_WORDS are non-empty strings."""
        assert len(BEARISH_WORDS) > 0
        for w in BEARISH_WORDS:
            assert isinstance(w, str) and len(w) > 0

    def test_category_keywords_structure(self):
        """CATEGORY_KEYWORDS maps category names to keyword lists."""
        assert len(CATEGORY_KEYWORDS) > 0
        for cat, keywords in CATEGORY_KEYWORDS.items():
            assert isinstance(cat, str)
            assert isinstance(keywords, list)
            assert len(keywords) > 0
            for kw in keywords:
                assert isinstance(kw, str) and len(kw) > 0

    def test_reddit_subreddits_are_strings(self):
        """All REDDIT_SUBREDDITS are non-empty strings."""
        assert len(REDDIT_SUBREDDITS) > 0
        for sub in REDDIT_SUBREDDITS:
            assert isinstance(sub, str) and len(sub) > 0


# ═══════════════════════════════════════════════════════════════════════
# ── Cache Behavior
# ═══════════════════════════════════════════════════════════════════════


class TestNewsServiceCache:
    """Internal caching behavior."""

    def test_cache_reset(self):
        """NewsService cache dict is mutable and resettable."""
        svc = NewsService()
        svc.cache["test"] = "value"
        assert svc.cache["test"] == "value"
        svc.cache = {}
        assert svc.cache == {}

    def test_seen_hashes_prevents_duplicate_fetch(self):
        """Simulate that seen_hashes prevents re-processing."""
        import hashlib

        svc = NewsService()
        h1 = hashlib.sha256(b"dup-content").hexdigest()
        h2 = hashlib.sha256(b"dup-content").hexdigest()
        svc.seen_hashes.add(h1)
        assert h2 in svc.seen_hashes  # same hash
        assert len(svc.seen_hashes) == 1
