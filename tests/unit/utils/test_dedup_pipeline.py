"""Orchestrator contract: level order, returned fields, bound search seam."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

from app.utils.dedup import (
    bind_title_search,
    check_duplicates,
    content_fingerprint,
    is_duplicate_by_title,
    mark_content_ingested,
    mark_url_ingested,
    new_group_id,
    record_fp_group,
    record_group_membership,
    reset_title_search,
)

URL = "https://example.com/eth-etf"
BODY = "Uniswap and Aave rally as $UNI holders watch Vitalik Buterin speak."
SOURCE_URL = "https://source.example/story-1"


@dataclass
class Hit:
    """Stand-in for the engine search hit."""

    doc_id: str
    score: float
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def make_search(hits: list[Hit], calls: list[tuple[str, str, int]] | None = None) -> Any:
    async def search(title: str, collection: str, limit: int) -> list[Hit]:
        if calls is not None:
            calls.append((title, collection, limit))
        return hits

    return search


async def failing_search(*_args: Any, **_kwargs: Any) -> list[Hit]:
    raise RuntimeError("qdrant unreachable")


@contextmanager
def use_search(fn: Any) -> Iterator[Any]:
    """Bind *fn* as the title search for the block, then reset the token."""
    token = bind_title_search(fn)
    try:
        yield fn
    finally:
        reset_title_search(token)


async def test_url_level_wins_and_returns_existing_group(fake_redis: Any) -> None:
    mark_url_ingested(URL)
    record_group_membership("grp:existing", URL, "news_v2")
    with use_search(make_search([])):
        result = await check_duplicates(URL, "title", BODY)
    assert result.is_duplicate is True
    assert result.level == "url"
    assert result.group_id == "grp:existing"


async def test_url_level_beats_content_and_search(fake_redis: Any) -> None:
    mark_url_ingested(URL)
    mark_content_ingested(content_fingerprint(BODY), "news_v2")
    calls: list[tuple[str, str, int]] = []
    with use_search(make_search([], calls)):
        result = await check_duplicates(URL, "title", BODY)
    assert result.level == "url"
    assert calls == []


async def test_content_level_runs_before_search(fake_redis: Any) -> None:
    fp = content_fingerprint(BODY)
    mark_content_ingested(fp, "news_v2")
    calls: list[tuple[str, str, int]] = []
    with use_search(make_search([], calls)):
        result = await check_duplicates(URL, "title", BODY)
    assert result.is_duplicate is True
    assert result.level == "content"
    assert result.details["fp"] == fp
    assert calls == []


async def test_content_group_is_returned(fake_redis: Any) -> None:
    fp = content_fingerprint(BODY)
    mark_content_ingested(fp, "news_v2")
    record_fp_group(fp, "grp:fp", "news_v2")
    with use_search(make_search([])):
        result = await check_duplicates(URL, "title", BODY)
    assert result.group_id == "grp:fp"


async def test_title_level_returns_matched_doc_and_score(fake_redis: Any) -> None:
    hit = Hit("doc-1", 0.99, metadata={"group_id": "grp:abc"})
    with use_search(make_search([hit])):
        result = await check_duplicates(URL, "title", BODY)
    assert result.is_duplicate is True
    assert result.level == "title"
    assert result.matched_doc_id == "doc-1"
    assert result.group_id == "grp:abc"
    assert result.details["score"] == 0.99


async def test_title_level_falls_back_to_source_url_group(fake_redis: Any) -> None:
    mark_url_ingested(SOURCE_URL)
    record_group_membership("grp:src", SOURCE_URL, "news_v2")
    hit = Hit("doc-2", 0.97, metadata={"source_url": SOURCE_URL})
    with use_search(make_search([hit])):
        result = await check_duplicates(URL, "title", BODY)
    assert result.level == "title"
    assert result.group_id == "grp:src"


async def test_title_threshold_boundary(fake_redis: Any) -> None:
    with use_search(make_search([Hit("d1", 0.95)])):
        at_threshold = await check_duplicates(URL, "t", BODY)
    with use_search(make_search([Hit("d2", 0.94)])):
        below = await check_duplicates(URL, "t", BODY)
    assert at_threshold.level == "title"
    assert below.is_duplicate is False


async def test_entity_level_inside_window(fake_redis: Any) -> None:
    hit = Hit("doc-3", 0.5, text=BODY, metadata={"published_at": "2026-10-25T11:00:00Z"})
    with use_search(make_search([hit])):
        result = await check_duplicates(URL, "title", BODY, published_at="2026-10-25T10:00:00Z")
    assert result.is_duplicate is True
    assert result.level == "entity"
    assert result.matched_doc_id == "doc-3"


async def test_entity_level_outside_window_is_new_story(fake_redis: Any) -> None:
    hit = Hit("doc-4", 0.5, text=BODY, metadata={"published_at": "2026-10-25T20:00:00Z"})
    with use_search(make_search([hit])):
        result = await check_duplicates(URL, "title", BODY, published_at="2026-10-25T10:00:00Z")
    assert result.is_duplicate is False
    assert result.group_id == new_group_id(URL)


async def test_search_failure_fails_open(fake_redis: Any) -> None:
    with use_search(failing_search):
        result = await check_duplicates(URL, "title", BODY)
    assert result.is_duplicate is False
    assert result.level is None
    assert result.group_id == new_group_id(URL)


async def test_redis_outage_fails_open(dead_redis: Any) -> None:
    with use_search(make_search([])):
        result = await check_duplicates(URL, "title", BODY)
    assert result.is_duplicate is False
    assert result.group_id == new_group_id(URL)


async def test_search_seam_receives_arguments(fake_redis: Any) -> None:
    calls: list[tuple[str, str, int]] = []
    with use_search(make_search([], calls)):
        await check_duplicates(URL, "the title", BODY, collection="custom")
    assert calls == [("the title", "custom", 5)]


async def test_empty_inputs_are_new_story(fake_redis: Any) -> None:
    with use_search(make_search([])):
        result = await check_duplicates("", "", "")
    assert result.is_duplicate is False
    assert result.level is None
    assert result.group_id == new_group_id("")
    assert result.details["fp"] == content_fingerprint("")


async def test_title_helper_uses_search_seam(fake_redis: Any) -> None:
    assert await is_duplicate_by_title("t", search=make_search([Hit("d", 0.96)])) is True
    assert await is_duplicate_by_title("t", search=make_search([Hit("d", 0.10)])) is False
    assert await is_duplicate_by_title("") is False  # blank title → no search
