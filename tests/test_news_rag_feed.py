"""Tests for GET /api/v1/news/rag-feed — the RAG-served consumer feed.

Covers: reranked lane (rugbrain up), fallback lane (rugbrain down →
recency-ranked news_v2, reranked=false), own-source priority boost,
and honest empty state (no fabricated articles).
"""

from __future__ import annotations

import os

os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"

from unittest.mock import AsyncMock, patch

import pytest

from app.routers.news import _is_own_source, _own_source_first, get_news_rag_feed


def _point(news_id: str, source: str, published_at: str, title: str) -> dict:
    return {
        "id": f"pt-{news_id}",
        "payload": {
            "content": f"body of {title}",
            "metadata": {
                "news_id": news_id,
                "title": title,
                "source": source,
                "url": f"https://example.com/{news_id}",
                "published_at": published_at,
                "category": "crypto",
                "image_url": None,
            },
        },
    }


POINTS = [
    _point("rss:aaa", "CoinTelegraph", "2026-08-31T10:00:00+00:00", "Aggregator story"),
    _point("tg:bbb", "KOL Trending (@CallAnalyser)", "2026-08-31T08:00:00+00:00", "KOL call"),
    _point("lookonchain:ccc", "lookonchain", "2026-08-30T12:00:00+00:00", "Whale move"),
]


class TestOwnSource:
    def test_news_id_prefixes(self):
        assert _is_own_source({"news_id": "tg:abc"})
        assert _is_own_source({"news_id": "lookonchain:abc"})
        assert _is_own_source({"news_id": "scanner:abc"})
        assert not _is_own_source({"news_id": "rss:abc"})
        assert not _is_own_source({"news_id": "cryptopanic:abc"})

    def test_source_markers(self):
        assert _is_own_source({"source": "RMI Scanner"})
        assert _is_own_source({"source": "PryScraper Intel"})
        assert not _is_own_source({"source": "CoinTelegraph"})

    def test_stable_partition(self):
        items = [
            {"news_id": "rss:1", "source": "CoinTelegraph"},
            {"news_id": "tg:2", "source": "KOL"},
            {"news_id": "rss:3", "source": "Decrypt"},
            {"news_id": "lookonchain:4", "source": "lookonchain"},
        ]
        out = _own_source_first(items)
        assert [i["news_id"] for i in out] == ["tg:2", "lookonchain:4", "rss:1", "rss:3"]


class TestRagFeedRoute:
    @pytest.fixture
    def route(self):
        return get_news_rag_feed

    async def test_reranked_lane(self, route):
        reranked_items = [
            {
                "id": "news-x",
                "news_id": "rss:x",
                "title": "Reranked story",
                "source": "Decrypt",
                "url": "https://example.com/x",
                "published_at": "2026-08-31T09:00:00+00:00",
                "summary": "body",
                "image_url": "https://img/x.jpg",
                "category": "crypto",
                "score": 0.9,
                "reranked": True,
            }
        ]
        with patch(
            "app.integrations.rugbrain_client.rugbrain_news_rerank",
            new=AsyncMock(return_value=reranked_items),
        ):
            resp = await route(limit=10, category=None, query=None)
        assert resp.rag_served is True
        assert resp.reranked is True
        assert resp.articles[0].title == "Reranked story"
        assert resp.articles[0].image_url == "https://img/x.jpg"

    async def test_fallback_lane_recency(self, route):
        """rugbrain down → recency-ranked news_v2, reranked=false, honest."""
        with (
            patch(
                "app.integrations.rugbrain_client.rugbrain_news_rerank",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.routers.news._scroll_news_v2",
                new=AsyncMock(return_value=POINTS),
            ),
        ):
            resp = await route(limit=10, category=None, query=None)
        assert resp.reranked is False
        assert resp.rag_served is True
        titles = [a.title for a in resp.articles]
        # Own-source first (stable), aggregator last — recency within groups.
        assert titles == ["KOL call", "Whale move", "Aggregator story"]

    async def test_fallback_own_source_priority(self, route):
        """Own-source items rank above newer aggregator news."""
        with (
            patch(
                "app.integrations.rugbrain_client.rugbrain_news_rerank",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.routers.news._scroll_news_v2",
                new=AsyncMock(return_value=POINTS),
            ),
        ):
            resp = await route(limit=10, category=None, query=None)
        titles = [a.title for a in resp.articles]
        assert titles.index("KOL call") < titles.index("Aggregator story")
        assert titles.index("Whale move") < titles.index("Aggregator story")
        assert resp.own_source_count == 2

    async def test_empty_corpus_honest_empty(self, route):
        """No points + no rerank → explicit empty feed, never fabricated."""
        with (
            patch(
                "app.integrations.rugbrain_client.rugbrain_news_rerank",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.routers.news._scroll_news_v2",
                new=AsyncMock(return_value=[]),
            ),
        ):
            resp = await route(limit=10, category=None, query=None)
        assert resp.total == 0
        assert resp.articles == []
        assert resp.status == "success"
