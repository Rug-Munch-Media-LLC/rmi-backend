"""Ghost → news_items join: mapping + idempotency contract."""

from __future__ import annotations

from datetime import UTC, datetime

from app.domains.news.ghost_sync import _row, _to_text


def test_to_text_strips_html():
    assert _to_text("<p>Hello&nbsp;<b>world</b></p>") == "Hello world"
    assert _to_text(None) == ""


def test_row_maps_post_to_news_item():
    row = _row(
        {
            "id": "abc123",
            "title": "  Daily Brief ",
            "url": "https://rugmunch.io/daily-brief/",
            "excerpt": "<p>Short <em>excerpt</em></p>",
            "feature_image": "https://rugmunch.io/img.png",
            "published_at": "2026-10-20T10:00:00.000Z",
        }
    )
    assert row is not None
    assert row["news_id"] == "ghost:abc123"
    assert row["title"] == "Daily Brief"
    assert row["source"] == "Ghost"
    assert row["source_type"] == "ghost"
    assert row["summary"] == "Short excerpt"
    assert row["published_at"] == datetime(2026, 10, 20, 10, 0, tzinfo=UTC)


def test_row_rejects_incomplete_posts():
    assert _row({"id": "x"}) is None
    assert _row({"id": "x", "title": "t", "url": "u"}) is None  # no published_at
