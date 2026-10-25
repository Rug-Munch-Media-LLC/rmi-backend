"""Pipeline contract, part 2: title/entity levels and the search seam."""

from __future__ import annotations

from typing import Any

from app.utils.dedup import (
    check_duplicates,
    is_duplicate_by_title,
    mark_url_ingested,
    new_group_id,
    record_group_membership,
)

from .dedup_doubles import Hit, failing_search, make_search, use_search

URL = "https://example.com/eth-etf"
BODY = "Uniswap and Aave rally as $UNI holders watch Vitalik Buterin speak."
SOURCE_URL = "https://source.example/story-1"


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


async def test_search_seam_receives_arguments(fake_redis: Any) -> None:
    calls: list[tuple[str, str, int]] = []
    with use_search(make_search([], calls)):
        await check_duplicates(URL, "the title", BODY, collection="custom")
    assert calls == [("the title", "custom", 5)]


async def test_title_helper_uses_search_seam(fake_redis: Any) -> None:
    assert await is_duplicate_by_title("t", search=make_search([Hit("d", 0.96)])) is True
    assert await is_duplicate_by_title("t", search=make_search([Hit("d", 0.10)])) is False
    assert await is_duplicate_by_title("") is False  # blank title → no search
