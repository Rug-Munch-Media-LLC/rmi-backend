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
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.rag.crawler import (
    crawl_and_ingest,
    crawl_crypto_page,
    discover_and_ingest,
    discover_crypto_news,
    filter_relevant,
    freshness_tier,
    is_fresh,
    is_relevant,
    prioritize_by_freshness,
    score_article_quality,
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
    dedup_result = SimpleNamespace(is_duplicate=False, level=None, group_id="grp:test", matched_doc_id=None, details={})

    with (
        patch("app.rag.engine.ingest_document", new=AsyncMock(return_value=ingest_result)) as ingest_mock,
        patch("app.rag.crawler.check_duplicates", new=AsyncMock(return_value=dedup_result)),
    ):
        result = await crawl_and_ingest(_URL)

    assert result["doc_id"] == "crawl:abc123"
    assert result["chunks"] == 4
    assert result["status"] == "ok"
    assert result["duplicate_level"] is None
    assert result["group_id"] == "grp:test"
    ingest_mock.assert_awaited_once()
    kwargs = ingest_mock.await_args.kwargs
    assert kwargs["collection"] == "news_v2"  # default collection
    assert kwargs["doc_id"].startswith("crawl:")  # stable hash id for the URL
    assert kwargs["content"] == "# Ingest me"
    assert kwargs["metadata"]["source_url"] == _URL
    assert kwargs["metadata"]["group_id"] == "grp:test"


@pytest.mark.asyncio
async def test_crawl_and_ingest_falls_back_to_computed_doc_id(mock_crawler) -> None:
    """If the engine omits doc_id, crawl_and_ingest uses its own hash id."""
    crawler, *_ = mock_crawler
    crawler.arun.return_value = _crawl_result(markdown="# X")
    dedup_result = SimpleNamespace(is_duplicate=False, level=None, group_id="grp:test", matched_doc_id=None, details={})

    with (
        patch("app.rag.engine.ingest_document", new=AsyncMock(return_value={"chunks": 1, "status": "ok"})),
        patch("app.rag.crawler.check_duplicates", new=AsyncMock(return_value=dedup_result)),
    ):
        result = await crawl_and_ingest(_URL)

    assert result["doc_id"].startswith("crawl:")
    assert result["status"] == "ok"


# ── Dedup integration ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crawl_and_ingest_url_duplicate_skips_ingest() -> None:
    """Level 1 (URL) duplicate → status=duplicate, no ingest, group linked."""
    dedup_result = SimpleNamespace(
        is_duplicate=True, level="url", group_id="grp:existing", matched_doc_id=None, details={}
    )

    with (
        patch("app.rag.engine.ingest_document", new=AsyncMock()) as ingest_mock,
        patch("app.rag.crawler.check_duplicates", new=AsyncMock(return_value=dedup_result)),
    ):
        result = await crawl_and_ingest(
            _URL,
            content="Dup content",
            metadata={"source_url": _URL, "crawler": "crawl4ai", "title": "T"},
        )

    ingest_mock.assert_not_awaited()
    assert result["status"] == "duplicate"
    assert result["duplicate_level"] == "url"
    assert result["group_id"] == "grp:existing"
    assert result["chunks"] == 0


@pytest.mark.asyncio
async def test_crawl_and_ingest_title_duplicate_skips_ingest() -> None:
    """Level 2 (title similarity) duplicate → status=duplicate, no ingest."""
    dedup_result = SimpleNamespace(
        is_duplicate=True, level="title", group_id="grp:existing", matched_doc_id="crawl:old", details={}
    )

    with (
        patch("app.rag.engine.ingest_document", new=AsyncMock()) as ingest_mock,
        patch("app.rag.crawler.check_duplicates", new=AsyncMock(return_value=dedup_result)),
    ):
        result = await crawl_and_ingest(
            _URL,
            content="Similar story body",
            metadata={"source_url": _URL, "crawler": "crawl4ai", "title": "T"},
        )

    ingest_mock.assert_not_awaited()
    assert result["status"] == "duplicate"
    assert result["duplicate_level"] == "title"
    assert result["group_id"] == "grp:existing"


@pytest.mark.asyncio
async def test_crawl_and_ingest_content_duplicate_skips_ingest() -> None:
    """Level 3 (content fingerprint) duplicate → status=duplicate, no ingest."""
    dedup_result = SimpleNamespace(
        is_duplicate=True, level="content", group_id="grp:existing", matched_doc_id=None, details={}
    )

    with (
        patch("app.rag.engine.ingest_document", new=AsyncMock()) as ingest_mock,
        patch("app.rag.crawler.check_duplicates", new=AsyncMock(return_value=dedup_result)),
    ):
        result = await crawl_and_ingest(
            _URL,
            content="Re-published body",
            metadata={"source_url": _URL, "crawler": "crawl4ai", "title": "T"},
        )

    ingest_mock.assert_not_awaited()
    assert result["status"] == "duplicate"
    assert result["duplicate_level"] == "content"


@pytest.mark.asyncio
async def test_crawl_and_ingest_entity_duplicate_skips_ingest() -> None:
    """Level 4 (entity overlap) duplicate → status=duplicate, no ingest."""
    dedup_result = SimpleNamespace(
        is_duplicate=True, level="entity", group_id="grp:existing", matched_doc_id="crawl:orig", details={}
    )

    with (
        patch("app.rag.engine.ingest_document", new=AsyncMock()) as ingest_mock,
        patch("app.rag.crawler.check_duplicates", new=AsyncMock(return_value=dedup_result)),
    ):
        result = await crawl_and_ingest(
            _URL,
            content="Same story other outlet",
            metadata={"source_url": _URL, "crawler": "crawl4ai", "title": "T"},
        )

    ingest_mock.assert_not_awaited()
    assert result["status"] == "duplicate"
    assert result["duplicate_level"] == "entity"
    assert result["group_id"] == "grp:existing"


@pytest.mark.asyncio
async def test_sweep_counts_duplicates_by_level() -> None:
    """Sweep summary breaks out url/title/content/entity duplicate counters."""
    published = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    discovered = [
        {
            "url": f"https://example.com/news/{i}",
            "title": f"Bitcoin hack story {i}",
            "published": published,
            "source": "https://example.com/feed.xml",
            "summary": "Bitcoin hack story.",
        }
        for i in range(4)
    ]
    page = {
        "title": "Bitcoin hack story",
        "content": "Bitcoin exchange hacked, funds stolen. " * 30,
        "metadata": {"source_url": "https://example.com/news/0", "crawler": "crawl4ai", "title": "Bitcoin hack story"},
    }
    levels = ["url", "title", "content", "entity"]
    seen: list[Any] = []

    async def _fake_crawl_and_ingest(url, **kwargs):
        level = levels[len(seen)]
        seen.append(url)
        return {
            "doc_id": f"crawl:{len(seen)}",
            "chunks": 0,
            "status": "duplicate",
            "duplicate_level": level,
            "group_id": "grp:existing",
        }

    with (
        patch("app.rag.crawler.discover_crypto_news", new=AsyncMock(return_value=discovered)),
        patch("app.rag.crawler.crawl_crypto_page", new=AsyncMock(return_value=page)),
        patch("app.rag.crawler.crawl_and_ingest", new=_fake_crawl_and_ingest),
    ):
        summary = await discover_and_ingest()

    assert summary["duplicates"] == 4
    assert summary["url_dup"] == 1
    assert summary["title_dup"] == 1
    assert summary["content_dup"] == 1
    assert summary["entity_dup"] == 1


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
    """No more than 10 entries are extracted per feed per sweep (rate limiting)."""
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
    assert len(items) == 10


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
    """A failing ingest is recorded in the sweep summary, not fatal."""
    published = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    discovered = [
        {
            "url": "https://example.com/broken",
            "title": "BTC exchange hack",
            "published": published,
            "source": "https://example.com/feed.xml",
            "summary": "A Bitcoin exchange was hacked and funds stolen.",
        }
    ]
    page = {
        "title": "BTC exchange hack",
        # Long content (>2000 chars) so the quality gate passes and the
        # failure comes from the ingest step.
        "content": "Bitcoin exchange hack details. " * 90,
        "metadata": {"source_url": "https://example.com/broken", "crawler": "crawl4ai", "title": "BTC exchange hack"},
    }
    with (
        patch("app.rag.crawler.discover_crypto_news", new=AsyncMock(return_value=discovered)),
        patch("app.rag.crawler.crawl_crypto_page", new=AsyncMock(return_value=page)),
        patch("app.rag.crawler.crawl_and_ingest", new=AsyncMock(side_effect=RuntimeError("ingest exploded"))),
    ):
        summary = await discover_and_ingest()

    assert summary["discovered"] == 1
    assert summary["relevant"] == 1
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


# ── Quality scoring ──────────────────────────────────────────────────────


def _article(**overrides: object) -> dict:
    """A minimal discovery item with a neutral (non-crypto) default title."""
    base: dict = {
        "url": "https://example.com/a",
        "title": "Untitled",
        "published": "",
        "source": "https://example.com/feed.xml",
        "summary": "",
    }
    base.update(overrides)
    return base


def test_score_article_quality_full_credit() -> None:
    """Long, paragraph-structured, non-paywalled crypto content scores 1.0."""
    chunk = "Bitcoin rallied as spot ETF inflows surged. "
    body = chunk * 50  # > 2000 chars
    content = "\n\n".join([body[:1200], body[1200:2400], body[2400:]])
    assert score_article_quality(_article(content=content)) == 1.0


def test_score_article_quality_medium_paragraph() -> None:
    """~640 char paragraph content: length + paragraphs + not paywalled + crypto."""
    content = "Ethereum staking yields are rising. " * 20  # ~640 chars
    content = "\n\n".join([content[:320], content[320:]])
    assert score_article_quality(_article(content=content)) == 0.8


def test_score_article_quality_paywalled_teaser() -> None:
    """A locked summary (paywall markers) never reaches the threshold."""
    assert score_article_quality(_article(content="Sign in to read this article.")) == 0.0


def test_score_article_quality_short_crypto_content_at_threshold() -> None:
    """Short crypto content scores exactly 0.3 — at (not below) the threshold."""
    assert score_article_quality(_article(content="BTC up")) == 0.3


def test_score_article_quality_empty() -> None:
    """Empty content scores 0.0 regardless of title."""
    assert score_article_quality(_article(content="")) == 0.0


# ── Relevance filter ─────────────────────────────────────────────────────


def test_is_relevant_token() -> None:
    assert is_relevant(_article(title="BTC breaks $70k")) is True


def test_is_relevant_defi() -> None:
    assert is_relevant(_article(title="New staking yield product launched")) is True


def test_is_relevant_security() -> None:
    assert is_relevant(_article(title="Protocol exploit drains $20M")) is True


def test_is_relevant_regulatory() -> None:
    assert is_relevant(_article(title="SEC sues exchange over unregistered securities")) is True


def test_is_relevant_rejects_nft_gaming_tech() -> None:
    """Off-topic content is filtered out unless it mentions crypto/defi/security."""
    assert is_relevant(_article(title="NFT drop announcement")) is False
    assert is_relevant(_article(title="Gaming studio raises round")) is False
    assert is_relevant(_article(title="New AI chip released")) is False
    assert is_relevant(_article(title="")) is False


def test_filter_relevant_keeps_only_matches() -> None:
    items = [
        _article(url="https://example.com/1", title="BTC rally"),
        _article(url="https://example.com/2", title="NFT drop announcement"),
    ]
    kept = filter_relevant(items)
    assert [i["url"] for i in kept] == ["https://example.com/1"]


# ── Freshness ────────────────────────────────────────────────────────────


def test_freshness_tier_fresh_recent_stale() -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    assert freshness_tier(_article(published="Mon, 03 Aug 2026 11:30:00 GMT"), now=now) == "fresh"
    assert freshness_tier(_article(published="Mon, 03 Aug 2026 09:00:00 GMT"), now=now) == "recent"
    assert freshness_tier(_article(published="Mon, 03 Aug 2026 05:00:00 GMT"), now=now) == "stale"
    assert freshness_tier(_article(published="garbage"), now=now) == "unknown"
    assert freshness_tier(_article(published=""), now=now) == "unknown"


def test_is_fresh_cutoff() -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    assert is_fresh(_article(published="Mon, 03 Aug 2026 11:30:00 GMT"), now=now) is True  # 0.5h
    assert is_fresh(_article(published="Mon, 03 Aug 2026 06:30:00 GMT"), now=now) is True  # 5.5h
    assert is_fresh(_article(published="Mon, 03 Aug 2026 05:30:00 GMT"), now=now) is False  # 6.5h
    assert is_fresh(_article(published=""), now=now) is True  # unknown → keep (deprioritised)


def test_is_fresh_respects_custom_window() -> None:
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)
    # 2h old vs a 3h window → fresh; vs a 1h window → stale.
    assert is_fresh(_article(published="Mon, 03 Aug 2026 10:00:00 GMT"), max_age_hours=3.0, now=now) is True
    assert is_fresh(_article(published="Mon, 03 Aug 2026 10:00:00 GMT"), max_age_hours=1.0, now=now) is False


def test_prioritize_by_freshness() -> None:
    items = [
        _article(url="https://example.com/old", published="Mon, 03 Aug 2026 05:00:00 GMT"),
        _article(url="https://example.com/unknown", published=""),
        _article(url="https://example.com/fresh", published="Mon, 03 Aug 2026 11:59:00 GMT"),
    ]
    ordered = prioritize_by_freshness(items)
    assert [i["url"] for i in ordered] == [
        "https://example.com/fresh",
        "https://example.com/old",
        "https://example.com/unknown",
    ]


# ── Sweep pipeline: quality + freshness gates ────────────────────────────


@pytest.mark.asyncio
async def test_sweep_rejects_low_quality() -> None:
    """Articles whose extracted content scores below 0.3 are skipped."""
    published = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    discovered = [
        {
            "url": "https://example.com/empty",
            "title": "Bitcoin hack",
            "published": published,
            "source": "https://example.com/feed.xml",
            "summary": "Bitcoin hack",
        }
    ]
    # Crawl returns no extractable content → quality 0.0 → rejected.
    page = {
        "title": "Bitcoin hack",
        "content": "",
        "metadata": {"source_url": "https://example.com/empty", "crawler": "crawl4ai", "title": "Bitcoin hack"},
    }
    with (
        patch("app.rag.crawler.discover_crypto_news", new=AsyncMock(return_value=discovered)),
        patch("app.rag.crawler.crawl_crypto_page", new=AsyncMock(return_value=page)),
        patch("app.rag.crawler.crawl_and_ingest", new=AsyncMock()) as ingest_mock,
    ):
        summary = await discover_and_ingest()

    ingest_mock.assert_not_awaited()
    assert summary["quality_rejected"] == 1
    assert summary["ingested"] == 0
    assert summary["attempted"] == 0


@pytest.mark.asyncio
async def test_sweep_skips_stale() -> None:
    """Articles older than max_age_hours are skipped before any crawl."""
    old = (datetime.now(UTC) - timedelta(hours=24)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    discovered = [
        {
            "url": "https://example.com/stale",
            "title": "Bitcoin hack",
            "published": old,
            "source": "https://example.com/feed.xml",
            "summary": "Bitcoin hack",
        }
    ]
    with (
        patch("app.rag.crawler.discover_crypto_news", new=AsyncMock(return_value=discovered)),
        patch("app.rag.crawler.crawl_crypto_page", new=AsyncMock()) as crawl_mock,
    ):
        summary = await discover_and_ingest()

    crawl_mock.assert_not_awaited()
    assert summary["stale_skipped"] == 1
    assert summary["attempted"] == 0


@pytest.mark.asyncio
async def test_sweep_filters_irrelevant_before_crawl() -> None:
    """Off-topic articles are dropped by the relevance gate before crawling."""
    published = (datetime.now(UTC) - timedelta(minutes=5)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    discovered = [
        {
            "url": "https://example.com/nft-drop",
            "title": "NFT drop announcement",
            "published": published,
            "source": "https://example.com/feed.xml",
            "summary": "A new collection is minting soon.",
        }
    ]
    with (
        patch("app.rag.crawler.discover_crypto_news", new=AsyncMock(return_value=discovered)),
        patch("app.rag.crawler.crawl_crypto_page", new=AsyncMock()) as crawl_mock,
    ):
        summary = await discover_and_ingest()

    crawl_mock.assert_not_awaited()
    assert summary["relevant"] == 0
    assert summary["attempted"] == 0


@pytest.mark.asyncio
async def test_crawl_and_ingest_with_supplied_content(mock_crawler) -> None:
    """crawl_and_ingest skips the crawl when content is pre-supplied (no double crawl)."""
    crawler, *_ = mock_crawler
    ingest_result = {"doc_id": "crawl:abc", "chunks": 2, "status": "ok"}
    dedup_result = SimpleNamespace(is_duplicate=False, level=None, group_id="grp:test", matched_doc_id=None, details={})

    with (
        patch("app.rag.engine.ingest_document", new=AsyncMock(return_value=ingest_result)) as ingest_mock,
        patch("app.rag.crawler.check_duplicates", new=AsyncMock(return_value=dedup_result)),
    ):
        result = await crawl_and_ingest(
            _URL,
            content="Pre-crawled body",
            metadata={"source_url": _URL, "crawler": "crawl4ai", "title": "X"},
        )

    assert result["status"] == "ok"
    ingest_mock.assert_awaited_once()
    kwargs = ingest_mock.await_args.kwargs
    assert kwargs["content"] == "Pre-crawled body"
    assert kwargs["metadata"]["source_url"] == _URL
    assert kwargs["metadata"]["group_id"] == "grp:test"
    crawler.arun.assert_not_awaited()  # content supplied → no second crawl
