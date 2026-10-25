"""Unknown published_at must stay unknown and never count as fresh."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.domains.news.freshness import resolve_published_at
from app.domains.news.freshness_window import FRESH, STALE, UNKNOWN, freshness

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "news_entries_freshness.json"
INGESTED = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _entries() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def test_undated_entry_has_no_published_at() -> None:
    assert resolve_published_at({"title": "no date"}) is None
    assert resolve_published_at({"title": "null date", "published": None}) is None


def test_dated_entry_is_parsed() -> None:
    got = resolve_published_at({"published": [2026, 10, 25, 9, 0, 0]})
    assert got == datetime(2026, 10, 25, 9, 0, tzinfo=UTC)


def test_unknown_is_not_fresh_and_excluded_from_fresh_share() -> None:
    buckets = [freshness(resolve_published_at(e), INGESTED) for e in _entries()]
    assert buckets.count(UNKNOWN) == 4
    assert buckets.count(STALE) == 2
    known = [b for b in buckets if b != UNKNOWN]
    assert buckets.count(FRESH) == 4
    # fresh share of KNOWN items (4/6), never of all items (4/10)
    assert round(100 * buckets.count(FRESH) / len(known)) == 67
    assert round(100 * buckets.count(FRESH) / len(buckets)) == 40


def test_future_garbage_is_unknown() -> None:
    assert freshness(datetime(2026, 10, 27, 0, 0, tzinfo=UTC), INGESTED) == UNKNOWN
