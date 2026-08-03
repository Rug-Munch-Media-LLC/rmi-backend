"""Unit tests for the RAG crawler (``app.rag.crawler``).

Covers the Crawl4AI integration:

  1. ``crawl_crypto_page`` extracts ``{title, content, metadata}`` from a
     Crawl4AI result (Crawl4AI classes mocked — no browser ever launches).
  2. ``crawl_and_ingest`` delegates to the RAG engine and mirrors its
     ``{doc_id, chunks, status}`` shape.
  3. ``discover_crypto_news`` parses RSS feeds via feedparser and skips
     invalid links / bozo feeds.
  4. Crawl4AI unavailability produces a clear ``RuntimeError`` (the caller
     chain in ``app.rag.pryscraper_ingest`` turns that into graceful
     fallback), and ``discover_and_ingest`` contains per-URL failures in
     its summary instead of aborting the sweep.
  5. JS-heavy pages: empty raw markdown falls back to ``extracted_content``.

All external dependencies (Crawl4AI, feedparser, the RAG engine) are mocked;
no network or browser access happens in these tests.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.rag.crawler import (
    crawl_and_ingest,
    crawl_crypto_page,
    discover_and_ingest,
    discover_crypto_news,
)

_URL = "https://example.com/crypto-article"


def _crawl_result(
    *,
    markdown: str = "# Hello\n\nCrawled content.",
    metadata: dict | None = None,
    status_code: int = 200,
    extracted_content: str = "",
) -> SimpleNamespace:
    """A fake CrawlResult/container in the shape Crawl4AI 0.8.x returns."""
    return SimpleNamespace(
        success=True,
        markdown=markdown,
        metadata=metadata or {"title": "Crawl4AI Test Article"},
        status_code=status_code,
        extracted_content=extracted_content,
        error_message=None,
    )


@pytest.fixture
def mock_crawler():
    """Patch the three Crawl4AI classes and hand back the fake crawler.

    The production code does ``from crawl4ai import AsyncWebCrawler,
    BrowserConfig, CrawlerRunConfig`` *inside* the function, so patching
    the ``crawl4ai`` module attributes intercepts the lazy import.
    """
    with (
        patch("crawl4ai.AsyncWebCrawler") as crawler_cls,
        patch("crawl4ai.BrowserConfig") as browser_cfg,
        patch("crawl4ai.CrawlerRunConfig") as run_cfg,
    ):
        crawler = AsyncMock()
        crawler_cls.return_value.__aenter__.return_value = crawler
        yield crawler, crawler_cls, browser_cfg, run_cfg


# ── Crawl ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawl_crypto_page_mocked(mock_crawler) -> None:
    """crawl_crypto_page returns {title, content, metadata} from Crawl4AI."""
    crawler, _, browser_cfg, run_cfg = mock_crawler
    crawler.arun.return_value = _crawl_result()

    result = await crawl_crypto_page(_URL)

    assert result["title"] == "Crawl4AI Test Article"
    assert result["content"] == "# Hello\n\nCrawled content."
    meta = result["metadata"]
    assert meta["source_url"] == _URL
    assert meta["crawler"] == "crawl4ai"
    assert meta["title"] == "Crawl4AI Test Article"
    assert meta["status_code"] == 200
    # The crawl actually went through Crawl4AI with a headless browser.
    crawler.arun.assert_awaited_once()
    browser_cfg.assert_called_once_with(headless=True)
    run_cfg.assert_called_once()


@pytest.mark.asyncio
async def test_crawl_and_ingest(mock_crawler) -> None:
    """crawl_and_ingest crawls then ingests and mirrors the engine result."""
    crawler, *_ = mock_crawler
    crawler.arun.return_value = _crawl_result(markdown="# Ingest me")
    ingest_result = {"doc_id": "crawl:abc123", "chunks": 4, "status": "ok"}

    with patch("app.rag.engine.ingest_document", new=AsyncMock(return_value=ingest_result)) as ingest_mock:
        result = await crawl_and_ingest(_URL)

    assert result == {"doc_id": "crawl:abc123", "chunks": 4, "status": "ok"}
    ingest_mock.assert_awaited_once()
    kwargs = ingest_mock.await_args.kwargs
    assert kwargs["collection"] == "news_v2"  # default collection
    assert kwargs["doc_id"].startswith("crawl:")  # stable hash id for the URL
    assert kwargs["content"] == "# Ingest me"
    assert kwargs["metadata"]["source_url"] == _URL


@pytest.mark.asyncio
async def test_crawl_and_ingest_falls_back_to_computed_doc_id(mock_crawler) -> None:
    """If the engine omits doc_id, crawl_and_ingest uses its own hash id."""
    crawler, *_ = mock_crawler
    crawler.arun.return_value = _crawl_result(markdown="# X")

    with patch("app.rag.engine.ingest_document", new=AsyncMock(return_value={"chunks": 1, "status": "ok"})):
        result = await crawl_and_ingest(_URL)

    assert result["doc_id"].startswith("crawl:")
    assert result["status"] == "ok"


# ── Discovery ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("parsed", "expected_links"),
    [
        # Normal feed: valid link extracted, non-http link dropped.
        (
            SimpleNamespace(
                bozo=False,
                entries=[
                    SimpleNamespace(
                        link="https://cointelegraph.com/news/1",
                        title="BTC rallies",
                        published="Mon, 03 Aug 2026",
                        summary="Bitcoin is up",
                    ),
                    SimpleNamespace(link="relative/path", title="bad", published="", summary=""),
                ],
            ),
            ["https://cointelegraph.com/news/1"],
        ),
        # Bozo feed with no entries → skipped entirely.
        (SimpleNamespace(bozo=True, entries=[], bozo_exception="bad xml"), []),
        # Bozo feed that still has entries → entries are still extracted.
        (
            SimpleNamespace(
                bozo=True,
                bozo_exception="recoverable",
                entries=[
                    SimpleNamespace(
                        link="https://decrypt.co/news/2",
                        title="ETH gas low",
                        published="",
                        summary="",
                    )
                ],
            ),
            ["https://decrypt.co/news/2"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_discover_crypto_news(parsed: SimpleNamespace, expected_links: list[str]) -> None:
    """RSS discovery extracts items from parsed feeds and filters bad links."""
    feed_url = "https://example.com/feed.xml"
    with patch("feedparser.parse", return_value=parsed) as parse_mock:
        items = await discover_crypto_news([feed_url])

    assert [i["url"] for i in items] == expected_links
    parse_mock.assert_called_once_with(feed_url)
    for item in items:
        assert item["source"] == feed_url
        assert item["title"] != ""
        assert item["url"].startswith(("http://", "https://"))


@pytest.mark.asyncio
async def test_discover_crypto_news_caps_entries_per_feed() -> None:
    """No more than 20 entries are extracted per feed per sweep."""
    entries = [
        SimpleNamespace(
            link=f"https://example.com/news/{i}",
            title=f"Item {i}",
            published="",
            summary="",
        )
        for i in range(30)
    ]
    parsed = SimpleNamespace(bozo=False, entries=entries)
    with patch("feedparser.parse", return_value=parsed):
        items = await discover_crypto_news(["https://example.com/feed.xml"])
    assert len(items) == 20


# ── Failure handling ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawler_graceful_on_failure() -> None:
    """Crawl4AI unavailable → clear RuntimeError, never an AttributeError crash.

    The crawler fails loudly with an actionable message. The fallback chain
    in ``app.rag.pryscraper_ingest`` catches this and degrades gracefully
    (covered by test_extract_falls_back_to_crawl4ai / test_extract_both_fail),
    and ``discover_and_ingest`` contains per-URL failures in its summary.
    """
    # Force `from crawl4ai import ...` to raise ImportError even though the
    # package is installed in this environment.
    with patch.dict(sys.modules, {"crawl4ai": None}), pytest.raises(RuntimeError, match="Crawl4AI is not installed"):
        await crawl_crypto_page(_URL)


@pytest.mark.asyncio
async def test_sweep_contains_item_failures() -> None:
    """A failing crawl is recorded in the sweep summary, not fatal."""
    discovered = [{"url": "https://example.com/broken"}]
    with (
        patch("app.rag.crawler.discover_crypto_news", new=AsyncMock(return_value=discovered)),
        patch("app.rag.crawler.crawl_and_ingest", new=AsyncMock(side_effect=RuntimeError("crawl exploded"))),
    ):
        summary = await discover_and_ingest()

    assert summary["discovered"] == 1
    assert summary["attempted"] == 1
    assert summary["failed"] == 1
    assert summary["ingested"] == 0
    assert "https://example.com/broken" in summary["errors"][0]


# ── JS-heavy pages ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawler_handles_js_heavy_pages(mock_crawler) -> None:
    """Empty raw markdown falls back to extracted_content (JS-rendered pages).

    SPA/Cloudflare pages often yield no raw markdown from Crawl4AI; the
    crawler falls back to the rendered ``extracted_content`` so content is
    still ingested.
    """
    crawler, *_ = mock_crawler
    crawler.arun.return_value = _crawl_result(
        markdown="",
        metadata={"title": "SPA Page"},
        extracted_content="<article>JS rendered content here</article>",
    )

    result = await crawl_crypto_page("https://js-heavy.example.com/spa")

    assert "JS rendered content here" in result["content"]
    assert result["metadata"]["crawler"] == "crawl4ai"


@pytest.mark.asyncio
async def test_crawl_failure_raises_runtime_error(mock_crawler) -> None:
    """A failed crawl (success=False) raises RuntimeError with the message."""
    crawler, *_ = mock_crawler
    crawler.arun.return_value = SimpleNamespace(
        success=False,
        error_message="blocked by cloudflare",
        markdown="",
        metadata={},
        status_code=403,
        extracted_content="",
    )

    with pytest.raises(RuntimeError, match="blocked by cloudflare"):
        await crawl_crypto_page("https://blocked.example.com")
