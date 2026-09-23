"""Scan-queue setup: parallel consumers + cached news fetch fallback (2026-09-23)."""

import asyncio

from app.domains.news.news_service import news_cache
from app.workers import scan_consumer


class _FakeNewsService:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch_all(self, **kwargs):
        self.calls += 1
        return {"articles": [], "call": self.calls}


def test_fetch_all_cached_serves_ttl_window(monkeypatch) -> None:
    fake = _FakeNewsService()
    monkeypatch.setattr(news_cache, "get_news_service", lambda: fake)
    news_cache.clear_cache()

    async def run():
        first = await news_cache.fetch_all_cached(limit=1)
        second = await news_cache.fetch_all_cached(limit=1)
        return first, second

    first, second = asyncio.run(run())
    assert fake.calls == 1, "second call inside the TTL window must be a cache hit"
    assert first is second


def test_scan_consumer_config_sane() -> None:
    assert scan_consumer.SCAN_CONCURRENCY >= 1
    assert scan_consumer.SCAN_QUEUE == "token_scan_queue"
