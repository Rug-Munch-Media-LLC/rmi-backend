"""Unit tests for the Pryscraper ↔ Crawl4AI bridge (``app.rag.pryscraper_ingest``).

Strategy under test (per 2026 crawl standards):
  1. Pryscraper first — fast HTML extraction.
  2. Crawl4AI fallback — JS-heavy pages, Cloudflare-protected sites.
  3. Both fail → RuntimeError with a clear message (never a raw exception).

The Pryscraper client and the Crawl4AI crawler are mocked; no network
access occurs in these tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.rag.pryscraper_ingest import extract_with_fallback

_URL = "https://example.com/article"


def _scrape_result(*, success: bool, markdown: str = "", error: str | None = None) -> SimpleNamespace:
    """A ``ScrapeResult``-shaped stand-in (success, data, error)."""
    return SimpleNamespace(success=success, data={"markdown": markdown} if markdown else {}, error=error)


@pytest.mark.asyncio
async def test_extract_with_pryscraper_first() -> None:
    """Pryscraper succeeds → its markdown is used, Crawl4AI never runs."""
    client = AsyncMock()
    client.scrape.return_value = _scrape_result(success=True, markdown="# Pry content")

    with (
        patch("app.services.pryscraper_client.PryscraperClient", return_value=client) as client_cls,
        patch("app.rag.crawler.crawl_crypto_page", new=AsyncMock()) as crawl_mock,
    ):
        content = await extract_with_fallback(_URL)

    assert content == "# Pry content"
    client_cls.assert_called_once_with()
    client.scrape.assert_awaited_once_with(_URL, formats=["markdown"], js_render=False)
    client.aclose.assert_awaited_once()  # client always closed, even on success
    crawl_mock.assert_not_awaited()  # fallback NOT used


@pytest.mark.parametrize(
    ("pryscraper_failure", "expected_crawl4ai_content"),
    [
        # Pryscraper returns success=False (e.g. blocked page)
        (
            _scrape_result(success=False, error="cloudflare blocked"),
            "# Crawl4AI rendered content",
        ),
        # Pryscraper returns success but empty content
        (
            _scrape_result(success=True, markdown="   "),
            "# Crawl4AI rendered content",
        ),
        # Pryscraper raises (service down / timeout)
        (
            RuntimeError("pry service unreachable"),
            "# Crawl4AI rendered content",
        ),
    ],
    ids=["failed-result", "empty-content", "raises"],
)
@pytest.mark.asyncio
async def test_extract_falls_back_to_crawl4ai(pryscraper_failure, expected_crawl4ai_content: str) -> None:
    """Pryscraper failure/empty → Crawl4AI is used and its content returned."""
    client = AsyncMock()
    if isinstance(pryscraper_failure, BaseException):
        client.scrape.side_effect = pryscraper_failure
    else:
        client.scrape.return_value = pryscraper_failure

    with (
        patch("app.services.pryscraper_client.PryscraperClient", return_value=client),
        patch(
            "app.rag.crawler.crawl_crypto_page",
            new=AsyncMock(return_value={"content": expected_crawl4ai_content}),
        ) as crawl_mock,
    ):
        content = await extract_with_fallback(_URL)

    assert content == expected_crawl4ai_content
    crawl_mock.assert_awaited_once_with(_URL)
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_extract_both_fail() -> None:
    """Both crawlers fail → RuntimeError naming the URL, not a crash."""
    client = AsyncMock()
    client.scrape.side_effect = RuntimeError("pry down")

    with (
        patch("app.services.pryscraper_client.PryscraperClient", return_value=client),
        patch("app.rag.crawler.crawl_crypto_page", new=AsyncMock(side_effect=RuntimeError("crawl4ai down"))),
        pytest.raises(RuntimeError, match="both crawlers failed") as exc_info,
    ):
        await extract_with_fallback(_URL)

    assert _URL in str(exc_info.value)
