"""Unit tests for the RAG story-ingest glue (``app/rag/story_ingest.py``).

Proves the RAG ingestion contract for "any story from any site":

  (a) ``fetch_story`` validates URLs and never raises for fetch failures —
      it returns ``StoryFetch(ok=False)`` with a "couldn't fetch" error;
  (b) the fetch chain is sequential, bounded, and fail-open — a dead layer
      moves to the next one;
  (c) ``ingest_url`` fetches + attributes the story and routes through the
      existing pipeline (``crawler.crawl_and_ingest``) with source
      attribution metadata;
  (d) ``ingest_url`` accepts pre-extracted content (fetch skipped).

Mock boundary: fetch layers and ``crawler.crawl_and_ingest`` are patched —
no live network, no Redis, no Qdrant.
"""

from __future__ import annotations

from unittest import mock

import pytest

from app.rag.story_ingest import (
    StoryFetch,
    StoryFetchError,
    _extract_meta_from_html,
    fetch_story,
    ingest_url,
)

URL = "https://decrypt.co/375622/sec-shelves-crypto-rule-meeting"


# ── fetch_story ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_story_bad_scheme_ok_false() -> None:
    story = await fetch_story("ftp://example.com/x")
    assert story.ok is False
    assert "http" in story.error


@pytest.mark.asyncio
async def test_fetch_story_all_layers_fail_returns_error_not_raise() -> None:
    with (
        mock.patch("app.rag.story_ingest._fetch_crawl4ai", new=mock.AsyncMock(return_value=None)),
        mock.patch("app.rag.story_ingest._fetch_flaresolverr", new=mock.AsyncMock(return_value=None)),
        mock.patch("app.rag.story_ingest._fetch_pryscraper", new=mock.AsyncMock(return_value=None)),
        mock.patch("app.rag.story_ingest._fetch_plain", new=mock.AsyncMock(return_value=None)),
    ):
        story = await fetch_story(URL, timeout=2.0, rotator=None)

    assert story.ok is False
    assert story.error == "couldn't fetch: all fetch layers failed"
    assert story.chain == ["crawl4ai", "flaresolverr", "pryscraper", "plain"]


@pytest.mark.asyncio
async def test_fetch_story_first_successful_layer_wins() -> None:
    with (
        mock.patch(
            "app.rag.story_ingest._fetch_crawl4ai",
            new=mock.AsyncMock(return_value=("crawl4ai body", "Title A", "Author A", "2026-08-14")),
        ) as c4,
        mock.patch("app.rag.story_ingest._fetch_flaresolverr", new=mock.AsyncMock()) as fs,
        mock.patch("app.rag.story_ingest._fetch_pryscraper", new=mock.AsyncMock()) as pry,
        mock.patch("app.rag.story_ingest._fetch_plain", new=mock.AsyncMock()) as plain,
    ):
        story = await fetch_story(URL, rotator=None)

    c4.assert_awaited_once()
    fs.assert_not_awaited()
    pry.assert_not_awaited()
    plain.assert_not_awaited()
    assert story.ok is True
    assert story.source == "crawl4ai"
    assert story.text == "crawl4ai body"
    assert story.title == "Title A"
    assert story.author == "Author A"
    assert story.published_at == "2026-08-14"


@pytest.mark.asyncio
async def test_fetch_story_falls_through_to_next_layer() -> None:
    with (
        mock.patch("app.rag.story_ingest._fetch_crawl4ai", new=mock.AsyncMock(return_value=None)),
        mock.patch("app.rag.story_ingest._fetch_flaresolverr", new=mock.AsyncMock(return_value=None)),
        mock.patch(
            "app.rag.story_ingest._fetch_pryscraper",
            new=mock.AsyncMock(return_value=("pry body", "Title P", "", "2026-08-13T10:00:00Z")),
        ),
        mock.patch("app.rag.story_ingest._fetch_plain", new=mock.AsyncMock()) as plain,
    ):
        story = await fetch_story(URL, rotator=None)

    plain.assert_not_awaited()
    assert story.ok is True
    assert story.source == "pryscraper"
    assert story.published_at == "2026-08-13T10:00:00Z"


@pytest.mark.asyncio
async def test_fetch_story_overall_timeout_bounded() -> None:
    async def _slow() -> tuple[str, str, str, str] | None:
        import asyncio

        await asyncio.sleep(5.0)
        return ("late body", "", "", "")

    with (
        mock.patch("app.rag.story_ingest._fetch_crawl4ai", side_effect=_slow),
        mock.patch("app.rag.story_ingest._fetch_flaresolverr", new=mock.AsyncMock(return_value=None)),
        mock.patch("app.rag.story_ingest._fetch_pryscraper", new=mock.AsyncMock(return_value=None)),
        mock.patch("app.rag.story_ingest._fetch_plain", new=mock.AsyncMock(return_value=None)),
    ):
        story = await fetch_story(URL, timeout=0.5, rotator=None)

    assert story.ok is False
    assert "couldn't fetch" in story.error


# ── metadata extraction (pure) ────────────────────────────────────────────


def test_extract_meta_from_html() -> None:
    html = (
        "<html><head><title>Big Story | Example</title>"
        '<meta name="author" content="Jane Doe">'
        '<meta property="article:published_time" content="2026-08-14T09:00:00Z">'
        "</head><body><article>Body text here.</article></body></html>"
    )
    title, author, published = _extract_meta_from_html(html, URL)
    assert title == "Big Story | Example"
    assert author == "Jane Doe"
    assert published == "2026-08-14T09:00:00Z"


def test_extract_meta_empty_uses_hostname_not_fabrication() -> None:
    title, author, published = _extract_meta_from_html("<html><body>x</body></html>", URL)
    assert title == "decrypt.co"
    assert author == ""
    assert published == ""


# ── ingest_url ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_url_fetches_and_attributes() -> None:
    story = StoryFetch(
        url=URL,
        text="body text",
        title="Big Story",
        author="Jane Doe",
        published_at="2026-08-14T09:00:00Z",
        source="plain",
        chain=["crawl4ai", "flaresolverr", "pryscraper", "plain"],
    )
    with (
        mock.patch("app.rag.story_ingest.fetch_story", new=mock.AsyncMock(return_value=story)) as fs,
        mock.patch(
            "app.rag.crawler.crawl_and_ingest",
            new=mock.AsyncMock(return_value={"status": "ok", "chunks": 3}),
        ) as ci,
    ):
        result = await ingest_url(URL, source_name="Decrypt")

    fs.assert_awaited_once()
    assert result["status"] == "ok"
    _url, kwargs = ci.await_args
    assert kwargs["collection"] == "news_v2"
    assert kwargs["content"] == "body text"
    meta = kwargs["metadata"]
    assert meta["source_url"] == URL
    assert meta["source_name"] == "Decrypt"
    assert meta["author"] == "Jane Doe"
    assert meta["published_at"] == "2026-08-14T09:00:00Z"
    assert meta["fetch_chain"] == ["crawl4ai", "flaresolverr", "pryscraper", "plain"]
    assert meta["crawler"] == "story_ingest:plain"


@pytest.mark.asyncio
async def test_ingest_url_with_supplied_content_skips_fetch() -> None:
    with (
        mock.patch("app.rag.story_ingest.fetch_story", new=mock.AsyncMock()) as fs,
        mock.patch(
            "app.rag.crawler.crawl_and_ingest",
            new=mock.AsyncMock(return_value={"status": "ok", "chunks": 1}),
        ) as ci,
    ):
        result = await ingest_url(URL, content="pre-extracted", title="T", author="A")

    fs.assert_not_awaited()
    assert result["status"] == "ok"
    _url, kwargs = ci.await_args
    assert kwargs["content"] == "pre-extracted"
    assert kwargs["metadata"]["fetch_chain"] == ["supplied"]
    assert kwargs["metadata"]["title"] == "T"
    assert kwargs["metadata"]["author"] == "A"


@pytest.mark.asyncio
async def test_ingest_url_fetch_failure_raises_clean_error() -> None:
    failed = StoryFetch(url=URL, chain=["plain"], error="couldn't fetch: all fetch layers failed")
    with (
        mock.patch("app.rag.story_ingest.fetch_story", new=mock.AsyncMock(return_value=failed)),
        mock.patch("app.rag.crawler.crawl_and_ingest", new=mock.AsyncMock()) as ci,
        pytest.raises(StoryFetchError),
    ):
        await ingest_url(URL)
    ci.assert_not_awaited()
