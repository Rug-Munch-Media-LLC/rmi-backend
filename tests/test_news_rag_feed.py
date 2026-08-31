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

from app.routers.news import (
    _BRANDED_PLACEHOLDER_IMAGE,
    _COMMENTARY_CACHE,
    _attach_commentary,
    _backfill_images,
    _generate_commentary,
    _is_own_source,
    _log_ingest_latency,
    _own_source_first,
    get_news_rag_feed,
)


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


@pytest.fixture(autouse=True)
def _no_og_network(monkeypatch):
    """Image doctrine backfill must be hermetic in tests: og:image fetches
    resolve to "" (→ branded placeholder) unless a test overrides."""

    async def _no_fetch(url: str, timeout: float = 6.0) -> str:
        return ""

    monkeypatch.setattr("app.domains.news.og_image.fetch_og_image", _no_fetch)


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


class TestImageBackfill:
    """Image doctrine (2026-08-31): every rag-feed item carries an image."""

    async def test_missing_image_gets_placeholder(self, monkeypatch):
        items = [{"id": "a", "url": "https://example.com/a", "image_url": None}]
        await _backfill_images(items)
        assert items[0]["image_url"] == _BRANDED_PLACEHOLDER_IMAGE

    async def test_carried_image_untouched(self):
        items = [{"id": "a", "url": "https://example.com/a", "image_url": "https://img/x.jpg"}]
        await _backfill_images(items)
        assert items[0]["image_url"] == "https://img/x.jpg"

    async def test_og_image_fetched_and_cached(self, monkeypatch):
        calls: list[str] = []

        async def _og(url: str, timeout: float = 6.0) -> str:
            calls.append(url)
            return "https://og.example/a.png"

        monkeypatch.setattr("app.domains.news.og_image.fetch_og_image", _og)
        items = [{"id": "a", "url": "https://example.com/a", "image_url": None}]
        await _backfill_images(items)
        assert items[0]["image_url"] == "https://og.example/a.png"
        # Second pass: served from cache, no second network call.
        items2 = [{"id": "a", "url": "https://example.com/a", "image_url": None}]
        await _backfill_images(items2)
        assert items2[0]["image_url"] == "https://og.example/a.png"
        assert calls == ["https://example.com/a"]

    async def test_og_failure_falls_back_to_placeholder(self, monkeypatch):
        async def _boom(url: str, timeout: float = 6.0) -> str:
            raise RuntimeError("down")

        monkeypatch.setattr("app.domains.news.og_image.fetch_og_image", _boom)
        items = [{"id": "a", "url": "https://example.com/a", "image_url": None}]
        await _backfill_images(items)
        assert items[0]["image_url"] == _BRANDED_PLACEHOLDER_IMAGE

    async def test_no_url_gets_placeholder(self):
        items = [{"id": "a", "url": "", "image_url": None}]
        await _backfill_images(items)
        assert items[0]["image_url"] == _BRANDED_PLACEHOLDER_IMAGE

    async def test_feed_route_always_returns_images(self):
        """End-to-end: fallback-lane items with image_url=None still ship images."""
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
            resp = await get_news_rag_feed(limit=10, category=None, query=None)
        assert resp.total == 3
        for a in resp.articles:
            assert a.image_url is not None
            assert a.image_url.startswith("https://")


class TestCommentary:
    def setup_method(self):
        _COMMENTARY_CACHE.clear()

    async def test_attach_commentary_uses_cache(self):
        _COMMENTARY_CACHE["rss:x"] = "Leverage is doing the talking again."
        items = [{"id": "news-x", "news_id": "rss:x", "title": "T"}]
        await _attach_commentary(items)
        assert items[0]["commentary"] == "Leverage is doing the talking again."

    async def test_attach_commentary_bounded_to_top_items(self):
        async def fake_gen(article):
            return f"commentary for {article['news_id']}"

        items = [{"id": f"n{i}", "news_id": f"rss:{i}", "title": f"t{i}"} for i in range(15)]
        with patch("app.routers.news._generate_commentary", new=fake_gen):
            await _attach_commentary(items)
        assert items[0]["commentary"] == "commentary for rss:0"
        assert items[9]["commentary"] == "commentary for rss:9"
        # Beyond the bounded window: no commentary attached.
        assert items[10].get("commentary") is None

    async def test_generate_commentary_fail_open(self):
        async def boom(*a, **k):
            raise RuntimeError("llm down")

        with patch("app.llm_config.chat_with_fallback", new=boom):
            out = await _generate_commentary({"news_id": "rss:boom", "title": "T"})
        assert out == ""


class TestIngestLatency:
    def test_no_timestamps_is_silent(self):
        _log_ingest_latency([{"id": "n1"}, {"id": "n2"}])  # must not raise

    @staticmethod
    def _spy_logger(monkeypatch):
        from unittest.mock import MagicMock

        spy = MagicMock()
        monkeypatch.setattr("app.routers.news.logger", spy)
        return spy

    def test_fresh_items_no_violation(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        spy = self._spy_logger(monkeypatch)
        fresh = datetime.now(UTC) - timedelta(seconds=30)
        _log_ingest_latency([{"published_at": fresh.isoformat()}])
        for call in spy.warning.call_args_list:
            assert "target_exceeded" not in str(call.args[0] if call.args else call)

    def test_stale_item_logs_violation(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        spy = self._spy_logger(monkeypatch)
        stale = datetime.now(UTC) - timedelta(minutes=12)
        _log_ingest_latency([{"published_at": stale.isoformat()}])
        assert spy.warning.called
        assert "target_exceeded" in str(spy.warning.call_args)
