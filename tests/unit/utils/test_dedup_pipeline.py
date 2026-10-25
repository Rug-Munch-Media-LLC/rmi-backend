"""Pipeline contract, part 1: URL and content levels short-circuit the search."""

from __future__ import annotations

from typing import Any

from app.utils.dedup import (
    check_duplicates,
    content_fingerprint,
    mark_content_ingested,
    mark_url_ingested,
    new_group_id,
    record_fp_group,
    record_group_membership,
)

from .dedup_doubles import make_search, use_search

URL = "https://example.com/eth-etf"
BODY = "Uniswap and Aave rally as $UNI holders watch Vitalik Buterin speak."


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


async def test_redis_outage_fails_open(dead_redis: Any) -> None:
    with use_search(make_search([])):
        result = await check_duplicates(URL, "title", BODY)
    assert result.is_duplicate is False
    assert result.group_id == new_group_id(URL)


async def test_empty_inputs_are_new_story(fake_redis: Any) -> None:
    with use_search(make_search([])):
        result = await check_duplicates("", "", "")
    assert result.is_duplicate is False
    assert result.level is None
    assert result.group_id == new_group_id("")
    assert result.details["fp"] == content_fingerprint("")
