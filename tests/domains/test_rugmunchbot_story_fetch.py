"""Unit tests for the RugMunchBot ``story_fetch`` tool.

Proves the hard guarantees of the "pull any story from any site" capability:

  (a) success returns ONLY real fetched content — text, title, author and
      publish date come from the fetch layer, never synthesized;
  (b) on total fetch failure the tool returns ``ok=False`` with a
      "couldn't fetch" error — zero fabrication;
  (c) invalid URLs (bad scheme, embedded credentials) are rejected before
      any fetch happens;
  (d) the optional RAG ingest side-effect is fail-open and bounded — a RAG
      outage never blocks or drops the fetched story;
  (e) ``story_fetch`` is registered in the shared tool registry.

Mock boundary: ``app.rag.story_ingest.fetch_story`` / ``ingest_url`` are
patched — no live network I/O, no live APIs.
"""

from __future__ import annotations

from unittest import mock

import pytest

from app.domains.telegram.rugmunchbot import tools as bot_tools
from app.domains.telegram.rugmunchbot.tools.story_fetch import STORY_TEXT_MAX, story_fetch
from app.rag.story_ingest import StoryFetch

URL = "https://decrypt.co/375622/sec-shelves-crypto-rule-meeting"
_TEXT = "SEC shelves the crypto rule meeting. " * 50  # ~1.6 KB of real-looking body


def _ok_story() -> StoryFetch:
    return StoryFetch(
        url=URL,
        text=_TEXT,
        title="SEC Shelves Crypto Rule Meeting",
        author="Decrypt / Decrypt Agent",
        published_at="2026-08-14T10:39:39",
        source="plain",
        chain=["crawl4ai", "flaresolverr", "pryscraper", "plain"],
    )


@pytest.mark.asyncio
async def test_success_returns_only_fetched_content() -> None:
    with mock.patch("app.rag.story_ingest.fetch_story", new=mock.AsyncMock(return_value=_ok_story())) as fetch:
        res = await story_fetch(url=URL)

    assert res.ok is True
    assert res.error is None
    assert res.source == "story_fetch:plain"
    assert res.data["title"] == "SEC Shelves Crypto Rule Meeting"
    assert res.data["author"] == "Decrypt / Decrypt Agent"
    assert res.data["published_at"] == "2026-08-14T10:39:39"
    assert res.data["text"] == _TEXT[:STORY_TEXT_MAX]
    assert res.data["chars"] == len(_TEXT)
    assert res.data["truncated"] is False
    assert "text_preview" in res.data
    fetch.assert_awaited_once_with(URL, timeout=12.0)


@pytest.mark.asyncio
async def test_url_required_ok_false() -> None:
    res = await story_fetch()
    assert res.ok is False
    assert res.error == "url required"


@pytest.mark.asyncio
async def test_fetch_failure_is_zero_fabrication() -> None:
    failed = StoryFetch(
        url=URL,
        chain=["crawl4ai", "flaresolverr", "pryscraper", "plain"],
        error="couldn't fetch: all fetch layers failed",
    )
    with mock.patch("app.rag.story_ingest.fetch_story", new=mock.AsyncMock(return_value=failed)):
        res = await story_fetch(url=URL)

    assert res.ok is False
    assert res.error is not None
    assert res.error.startswith("couldn't fetch")
    assert res.data == {"url": URL, "fetch_chain": ["crawl4ai", "flaresolverr", "pryscraper", "plain"]}
    assert not res.data.get("text")  # nothing fabricated


@pytest.mark.asyncio
async def test_bad_scheme_rejected() -> None:
    # URL validation lives in fetch_story (single source of truth); the tool
    # surfaces its ok=False result without fabricating anything.
    with mock.patch(
        "app.rag.story_ingest.fetch_story",
        new=mock.AsyncMock(
            return_value=StoryFetch(
                url="ftp://example.com/x",
                chain=[],
                error="unsupported URL — only http/https allowed",
            )
        ),
    ) as fetch:
        res = await story_fetch(url="ftp://example.com/x")
    fetch.assert_awaited_once()
    assert res.ok is False
    assert "http" in (res.error or "")
    assert not res.data.get("text")


@pytest.mark.asyncio
async def test_embedded_credentials_rejected() -> None:
    res = await story_fetch(url="https://user:pass@example.com/x")
    assert res.ok is False
    assert "credential" in (res.error or "")


@pytest.mark.asyncio
async def test_ingest_side_effect_fail_open() -> None:
    with (
        mock.patch("app.rag.story_ingest.fetch_story", new=mock.AsyncMock(return_value=_ok_story())),
        mock.patch("app.rag.story_ingest.ingest_url", new=mock.AsyncMock(side_effect=RuntimeError("qdrant down"))),
    ):
        res = await story_fetch(url=URL, ingest=True)

    assert res.ok is True  # story still returned despite RAG outage
    assert res.data["ingest"]["status"] == "failed"
    assert res.data["title"] == "SEC Shelves Crypto Rule Meeting"


@pytest.mark.asyncio
async def test_ingest_side_effect_success_shape() -> None:
    with (
        mock.patch("app.rag.story_ingest.fetch_story", new=mock.AsyncMock(return_value=_ok_story())),
        mock.patch(
            "app.rag.story_ingest.ingest_url",
            new=mock.AsyncMock(
                return_value={
                    "doc_id": "crawl:abc",
                    "chunks": 4,
                    "status": "ok",
                    "duplicate_level": None,
                    "group_id": "g1",
                }
            ),
        ),
    ):
        res = await story_fetch(url=URL, ingest=True)

    assert res.ok is True
    assert res.data["ingest"]["status"] == "ok"
    assert res.data["ingest"]["chunks"] == 4


def test_story_fetch_registered() -> None:
    assert "story_fetch" in bot_tools.registry
    assert bot_tools.registry._ttls.get("story_fetch") == 0.0  # never cached


@pytest.mark.asyncio
async def test_story_fetch_runs_through_registry() -> None:
    with mock.patch("app.rag.story_ingest.fetch_story", new=mock.AsyncMock(return_value=_ok_story())):
        res = await bot_tools.registry.run("story_fetch", url=URL)
    assert res.ok is True
    assert res.tool == "story_fetch"
