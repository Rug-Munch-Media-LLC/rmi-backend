"""Unit tests for news_sentiment — RugBrain news_v2 token sentiment engine.

Covers:
  - ``analyze_sentiment`` keyword classification (bullish / bearish /
    neutral / no-signal)
  - ``compute_trending_score`` recency weighting and cap
  - ``analyze_token_news_sentiment`` with mocked HybridRetriever
  - NO_DATA degradation (empty results, retrieval failure, no identity)

All external dependencies (RAG HybridRetriever) are mocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.scanner.engines.news_sentiment import (
    HIGH_MEDIA_ATTENTION_THRESHOLD,
    analyze_sentiment,
    analyze_token_news_sentiment,
    compute_trending_score,
)
from app.rag.retrieval import ScoredHit


def _hit(
    title: str,
    content: str,
    published_at: str,
    *,
    score: float = 0.5,
    final: float = 0.6,
    url: str = "https://example.com/article",
) -> ScoredHit:
    """Build a ScoredHit shaped like a news_v2 article."""
    return ScoredHit(
        content=f"Title: {title}\n\n{content}",
        score=score,
        final_score=final,
        metadata={
            "title": title,
            "source_url": url,
            "source_name": "Test News",
            "published_at": published_at,
            "source": "news",
        },
        collection="news_v2",
        doc_id=f"news:{title[:8]}",
    )


def _iso(hours_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


class TestAnalyzeSentiment:
    def test_bullish_keywords(self) -> None:
        assert analyze_sentiment("Bitcoin surges to new high, adoption rally") == ("BULLISH", 0.7)

    def test_bearish_keywords(self) -> None:
        assert analyze_sentiment("Token crashes after hack, SEC lawsuit") == ("BEARISH", 0.7)

    def test_mixed_returns_neutral(self) -> None:
        assert analyze_sentiment("Price surge after crash") == ("NEUTRAL", 0.5)

    def test_no_keywords_neutral_low_confidence(self) -> None:
        assert analyze_sentiment("The meeting starts at noon tomorrow") == ("NEUTRAL", 0.3)

    def test_empty_text_neutral_low_confidence(self) -> None:
        assert analyze_sentiment("") == ("NEUTRAL", 0.3)

    def test_bearish_dominates_small_bullish_noise(self) -> None:
        # 3 bearish hits vs 1 bullish hit → bearish (3 > 1*2)
        text = "hack exploit scam SEC lawsuit crash dump"
        assert analyze_sentiment(text)[0] == "BEARISH"


class TestComputeTrendingScore:
    def test_recent_articles_score_15_each(self) -> None:
        articles = [{"published_at": _iso(1)}, {"published_at": _iso(5)}]
        assert compute_trending_score(articles) == 30

    def test_older_articles_score_5_each(self) -> None:
        articles = [{"published_at": _iso(30)}, {"published_at": _iso(48)}]
        assert compute_trending_score(articles) == 10

    def test_very_old_articles_score_1_each(self) -> None:
        articles = [{"published_at": _iso(100)}, {"published_at": _iso(200)}]
        assert compute_trending_score(articles) == 2

    def test_mixed_recency(self) -> None:
        articles = [{"published_at": _iso(1)}, {"published_at": _iso(48)}, {"published_at": _iso(200)}]
        assert compute_trending_score(articles) == 21  # 15 + 5 + 1

    def test_missing_date_counts_as_old(self) -> None:
        assert compute_trending_score([{}, {"published_at": ""}]) == 2

    def test_capped_at_100(self) -> None:
        articles = [{"published_at": _iso(1)} for _ in range(10)]
        assert compute_trending_score(articles) == 100

    def test_empty_list_zero(self) -> None:
        assert compute_trending_score([]) == 0


class TestAnalyzeTokenNewsSentiment:
    @pytest.mark.asyncio
    async def test_bullish_scan(self) -> None:
        hits = [
            _hit("ETH surges to new high", "Ethereum adoption rally breakout", _iso(2), score=0.9, final=0.95),
            _hit("ETH mainnet upgrade", "Launch of mainnet upgrade", _iso(6), score=0.8, final=0.85),
            _hit("ETH market recap", "Quarterly market review scheduled", _iso(30), score=0.5, final=0.5),
        ]
        fake = AsyncMock()
        fake.retrieve.return_value = hits
        with patch("app.rag.retrieval.HybridRetriever", return_value=fake):
            result = await analyze_token_news_sentiment("ETH", "Ethereum", chain="ethereum")

        assert result["result"] == "scanned"
        assert result["sentiment"] == "BULLISH"
        assert result["article_count"] == 3
        assert result["sentiment_breakdown"] == {"positive": 2, "negative": 0, "neutral": 1}
        assert result["trending_score"] == 35  # 15 + 15 + 5 (30h → older window)
        assert len(result["recent_articles"]) == 3
        first = result["recent_articles"][0]
        assert first["title"] == "ETH surges to new high"
        assert first["url"] == "https://example.com/article"
        assert first["relevance"] == 0.95
        assert first["sentiment"] == "BULLISH"

    @pytest.mark.asyncio
    async def test_bearish_dominant(self) -> None:
        hits = [
            _hit("Token hacked", "Exploit vulnerability rugpull", _iso(3)),
            _hit("SEC lawsuit", "SEC lawsuit crash dump", _iso(4)),
            _hit("Token listed", "New listing launch", _iso(50)),
        ]
        fake = AsyncMock()
        fake.retrieve.return_value = hits
        with patch("app.rag.retrieval.HybridRetriever", return_value=fake):
            result = await analyze_token_news_sentiment("TOK", "Token", chain="ethereum")

        assert result["result"] == "scanned"
        assert result["sentiment"] == "BEARISH"
        assert result["sentiment_breakdown"] == {"positive": 1, "negative": 2, "neutral": 0}

    @pytest.mark.asyncio
    async def test_high_media_attention_flag(self) -> None:
        # 6 recent articles → trending 90 (>70) → HIGH_MEDIA_ATTENTION flag
        hits = [_hit("ETH news", "market rally", _iso(1)) for _ in range(6)]
        fake = AsyncMock()
        fake.retrieve.return_value = hits
        with patch("app.rag.retrieval.HybridRetriever", return_value=fake):
            result = await analyze_token_news_sentiment("ETH", "Ethereum")

        assert result["trending_score"] == 90
        assert result["trending_score"] > HIGH_MEDIA_ATTENTION_THRESHOLD
        assert result["flags"] == ["HIGH_MEDIA_ATTENTION"]

    @pytest.mark.asyncio
    async def test_no_articles_returns_no_data(self) -> None:
        fake = AsyncMock()
        fake.retrieve.return_value = []
        with patch("app.rag.retrieval.HybridRetriever", return_value=fake):
            result = await analyze_token_news_sentiment("UNKNOWN", "Unknown Token")

        assert result["result"] == "no_data"
        assert result["sentiment"] == "NO_DATA"
        assert result["article_count"] == 0
        assert result["trending_score"] == 0
        assert result["reason"] == "no_articles"

    @pytest.mark.asyncio
    async def test_retrieval_failure_returns_no_data(self) -> None:
        fake = AsyncMock()
        fake.retrieve.side_effect = RuntimeError("RAG down")
        with patch("app.rag.retrieval.HybridRetriever", return_value=fake):
            result = await analyze_token_news_sentiment("ETH", "Ethereum")

        assert result["result"] == "no_data"
        assert result["sentiment"] == "NO_DATA"
        assert result["reason"] == "retrieval_unavailable"

    @pytest.mark.asyncio
    async def test_no_identity_returns_no_data(self) -> None:
        result = await analyze_token_news_sentiment("", "")
        assert result["result"] == "no_data"
        assert result["sentiment"] == "NO_DATA"
        assert result["reason"] == "no_token_identity"

    @pytest.mark.asyncio
    async def test_query_uses_name_symbol_and_news_v2(self) -> None:
        fake = AsyncMock()
        fake.retrieve.return_value = []
        with patch("app.rag.retrieval.HybridRetriever", return_value=fake):
            await analyze_token_news_sentiment("PEPE", "Pepe the Frog")

        fake.retrieve.assert_awaited_once()
        kwargs = fake.retrieve.call_args.kwargs
        assert kwargs["query"] == "Pepe the Frog PEPE news"
        assert kwargs["collection"] == "news_v2"
        assert kwargs["top_k"] == 10
