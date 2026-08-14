"""Unit tests for the reranker-based news importance gate (channels/importance.py).

Covers:
  * composite formula: importance = max relevance across importance queries
  * threshold gating in rerank mode (``RMI_NEWS_IMPORTANCE_THRESHOLD``)
  * top-N selection + ordering (best first)
  * focus-keyword queries appended to the default query set
  * fail-open: reranker unavailable → deterministic heuristic fallback that
    keeps news flowing (top-N without the threshold)
  * text truncation and batch cap

No live network I/O — ``app.rag.rerank.rerank_results`` is patched at the
module boundary the scorer actually uses.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.domains.news_ingest.models import NewsArticle
from app.domains.telegram.rugmunchbot.channels.importance import (
    ScoredArticle,
    _heuristic_importance,
    _heuristic_scored,
    score_importance,
    select_important,
    top_headlines,
    trending_tokens,
)


def _article(title: str, body: str = "") -> NewsArticle:
    return NewsArticle(
        title=title,
        body=body or title,
        source_channel="CoinTelegraph",
        source_url="https://t.me/cointelegraph/1",
        category="crypto_news",
        content_hash="x",
    )


def _scored(title: str, importance: float, fallback: bool = False) -> ScoredArticle:
    return ScoredArticle(article=_article(title), importance=importance, best_query="q", fallback=fallback)


# ── Composite + rerank path ────────────────────────────────────────────────


class TestScoreImportance:
    async def test_composite_is_max_across_queries(self) -> None:
        articles = [_article("Bridge exploited"), _article("Meme coin pumps")]

        async def fake_rerank(query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
            # Simulate per-query relevance: first article strong on exploit
            # queries, second weak everywhere.
            if "exploit" in query or "hack" in query:
                return [{"index": 0, "relevance_score": 0.98}, {"index": 1, "relevance_score": 0.02}]
            return [{"index": 0, "relevance_score": 0.3}, {"index": 1, "relevance_score": 0.05}]

        with patch("app.rag.rerank.rerank_results", new=AsyncMock(side_effect=fake_rerank)):
            scored = await score_importance(articles)

        assert len(scored) == 2
        assert scored[0].importance == pytest.approx(0.98)
        assert scored[0].fallback is False
        assert scored[1].importance == pytest.approx(0.05)
        assert scored[0].best_query  # the query that produced the max

    async def test_focus_keywords_append_queries(self) -> None:
        articles = [_article("Whale moves 50k BTC")]
        seen_queries: list[str] = []

        async def fake_rerank(query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
            seen_queries.append(query)
            return [{"index": 0, "relevance_score": 0.6}]

        with patch("app.rag.rerank.rerank_results", new=AsyncMock(side_effect=fake_rerank)):
            await score_importance(articles, focus_keywords=["etf", "whale"])

        assert any("etf" in q for q in seen_queries)
        assert any("whale" in q for q in seen_queries)
        assert len(seen_queries) >= 2

    async def test_empty_articles_returns_empty(self) -> None:
        assert await score_importance([]) == []


# ── Fail-open: reranker down → heuristic fallback ──────────────────────────


class TestFallback:
    async def test_reranker_unavailable_falls_back_to_heuristic(self) -> None:
        articles = [_article("ETF approved by SEC"), _article("Random daily news")]

        async def empty_rerank(query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
            return []  # Infinity down / no scores

        with patch("app.rag.rerank.rerank_results", new=AsyncMock(side_effect=empty_rerank)):
            scored = await score_importance(articles)

        assert len(scored) == 2
        assert all(s.fallback for s in scored)
        # Heuristic still differentiates: ETF/regulatory story > generic filler.
        assert scored[0].importance > scored[1].importance

    async def test_rerank_exception_falls_back_to_heuristic(self) -> None:
        articles = [_article("Major bridge exploited, funds drained, $100M stolen")]

        async def boom(query: str, documents: list[str], top_n: int) -> list[dict[str, Any]]:
            raise RuntimeError("infinity unreachable")

        with patch("app.rag.rerank.rerank_results", new=AsyncMock(side_effect=boom)):
            scored = await score_importance(articles)

        assert len(scored) == 1
        assert scored[0].fallback is True
        assert scored[0].importance > 0.5  # strong exploit/drain/stolen signal

    def test_heuristic_importance_differentiates(self) -> None:
        exploit = _heuristic_importance(_article("Bridge exploited, funds drained"))
        noise = _heuristic_importance(_article("Crypto markets calm today"))
        assert exploit > 0.5
        assert noise < 0.5


# ── Selection + threshold ──────────────────────────────────────────────────


class TestSelectImportant:
    def test_rerank_mode_keeps_only_above_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.domains.telegram.rugmunchbot.channels.importance.IMPORTANCE_THRESHOLD", 0.4)
        scored = [_scored("ETF approved", 0.92), _scored("Meme coin", 0.001), _scored("Whale move", 0.59)]
        out = select_important(scored, top_n=5)
        assert [s.article.title for s in out] == ["ETF approved", "Whale move"]

    def test_rerank_mode_sorts_best_first_and_caps_top_n(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.domains.telegram.rugmunchbot.channels.importance.IMPORTANCE_THRESHOLD", 0.0)
        scored = [_scored("low", 0.2), _scored("mid", 0.5), _scored("high", 0.9)]
        out = select_important(scored, top_n=2)
        assert [s.article.title for s in out] == ["high", "mid"]

    def test_fallback_mode_bypasses_threshold_keeps_top_n(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.domains.telegram.rugmunchbot.channels.importance.IMPORTANCE_THRESHOLD", 0.8)
        scored = [_scored("a", 0.3, fallback=True), _scored("b", 0.5, fallback=True)]
        out = select_important(scored, top_n=2)
        # News still flows even though both are below the (ignored) threshold.
        assert len(out) == 2
        assert out[0].article.title == "b"

    def test_empty_scored_returns_empty(self) -> None:
        assert select_important([]) == []

    def test_heuristic_scored_marks_fallback(self) -> None:
        out = _heuristic_scored([_article("Exploit")])
        assert out[0].fallback is True
        assert out[0].best_query == "heuristic-fallback"


# ── Market-awareness helpers (top_headlines / trending_tokens) ─────────────


def _article_with_entities(title: str, entities: list[str]) -> NewsArticle:
    a = _article(title)
    a.entities = entities
    return a


class TestMarketAwareness:
    def test_top_headlines_orders_by_importance(self) -> None:
        scored = [_scored("Second", 0.3), _scored("First", 0.9), _scored("Third", 0.5)]
        out = top_headlines(scored, top_n=10)
        assert [a.title for a in out] == ["First", "Third", "Second"]

    def test_top_headlines_caps_and_preserves_importance_order(self) -> None:
        scored = [_scored(f"H{i}", 1.0 - i / 10.0) for i in range(5)]
        assert len(top_headlines(scored, top_n=2)) == 2
        assert [a.title for a in top_headlines(scored, top_n=2)] == ["H0", "H1"]

    def test_top_headlines_empty(self) -> None:
        assert top_headlines([], top_n=5) == []

    def test_trending_tokens_counts_and_dedupes(self) -> None:
        articles = [
            _article_with_entities("a", ["$BTC", "$SOL"]),
            _article_with_entities("b", ["$BTC"]),
            _article_with_entities("c", ["$sol", "$PEPE"]),  # lowercase normalizes
        ]
        out = trending_tokens(articles, top_n=8)
        assert out == ["BTC", "SOL", "PEPE"]  # BTC 2x, then alpha tiebreak

    def test_trending_tokens_caps_top_n(self) -> None:
        articles = [_article_with_entities(f"a{i}", [f"$T{i}"]) for i in range(5)]
        assert len(trending_tokens(articles, top_n=2)) == 2

    def test_trending_tokens_empty(self) -> None:
        assert trending_tokens([], top_n=8) == []
        assert trending_tokens([_article_with_entities("x", [])], top_n=8) == []
