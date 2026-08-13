"""PURE tests for the X/Twitter ingestion date-parsing gap.

Covers ``parse_timestamp`` (ISO-8601 with/without ms & tz, RFC 822/2822
``pubDate``, plain epoch int/float, garbage → None), ``ShillPost.from_social_post``
with an RFC 822 ``pubDate``, the X-ingestion path via a fake reader emitting
RFC 822 timestamps (correct ``ts``), the real-reader wrapper
``ingest_x_from_feed`` (via a monkeypatched ``get_twitter_feed``), and
fail-open ``[]`` on a raising reader. No network, no DB.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domains.shill_alert.models import ShillPost, parse_timestamp
from app.domains.shill_alert.service import ingest_x_from_feed, ingest_x_posts

EPOCH = datetime(2026, 8, 13, 10, 15, 0, tzinfo=UTC).timestamp()


# ── parse_timestamp ─────────────────────────────────────────────────────────


def test_parse_timestamp_iso_z() -> None:
    assert parse_timestamp("2026-08-13T10:15:00Z") == pytest.approx(EPOCH)


def test_parse_timestamp_iso_with_milliseconds() -> None:
    expected = datetime(2026, 8, 13, 10, 15, 0, 123000, tzinfo=UTC).timestamp()
    assert parse_timestamp("2026-08-13T10:15:00.123Z") == pytest.approx(expected)


def test_parse_timestamp_iso_without_tz_assumed_utc() -> None:
    assert parse_timestamp("2026-08-13T10:15:00") == pytest.approx(EPOCH)


def test_parse_timestamp_iso_with_offset() -> None:
    assert parse_timestamp("2026-08-13T10:15:00+00:00") == pytest.approx(EPOCH)
    assert parse_timestamp("2026-08-13T12:15:00+02:00") == pytest.approx(EPOCH)


def test_parse_timestamp_rfc822_gmt() -> None:
    assert parse_timestamp("Wed, 13 Aug 2026 10:15:00 GMT") == pytest.approx(EPOCH)


def test_parse_timestamp_rfc822_numeric_offset() -> None:
    assert parse_timestamp("Wed, 13 Aug 2026 10:15:00 +0000") == pytest.approx(EPOCH)


def test_parse_timestamp_epoch_int_and_float() -> None:
    assert parse_timestamp(1_750_000_000) == 1_750_000_000.0
    assert parse_timestamp(1_750_000_000.5) == 1_750_000_000.5


def test_parse_timestamp_epoch_milliseconds() -> None:
    assert parse_timestamp(1_750_000_000_000) == pytest.approx(1_750_000_000.0)
    assert parse_timestamp(1_750_000_000_123) == pytest.approx(1_750_000_000.123)


def test_parse_timestamp_numeric_string() -> None:
    assert parse_timestamp("1750000000") == 1_750_000_000.0


def test_parse_timestamp_garbage_is_none() -> None:
    assert parse_timestamp("not a date") is None
    assert parse_timestamp(None) is None
    assert parse_timestamp("") is None
    assert parse_timestamp("   ") is None
    assert parse_timestamp(True) is None  # bool is an int subclass — not a timestamp
    assert parse_timestamp([]) is None


# ── from_social_post with RFC 822 ───────────────────────────────────────────


def test_from_social_post_rfc822_pubdate_parses_epoch() -> None:
    raw = {
        "handle": "alice_x",
        "text": "$PEPE is a gem, buy now",
        "pubDate": "Wed, 13 Aug 2026 10:15:00 GMT",
        "url": "https://x.com/alice_x/status/1",
    }
    post = ShillPost.from_social_post(raw, "x")
    assert post.ts == pytest.approx(EPOCH)


def test_from_social_post_rfc822_published_key_parses_epoch() -> None:
    raw = {
        "handle": "alice_x",
        "text": "$PEPE is a gem, buy now",
        "published": "Wed, 13 Aug 2026 10:15:00 GMT",
    }
    post = ShillPost.from_social_post(raw, "x")
    assert post.ts == pytest.approx(EPOCH)


def test_from_social_post_garbage_ts_fails_open_to_zero() -> None:
    raw = {"handle": "alice_x", "text": "$PEPE is a gem", "published": "someday"}
    post = ShillPost.from_social_post(raw, "x")
    assert post.ts == 0.0  # fail-open, never raises


# ── ingest via a fake reader emitting RFC 822 ───────────────────────────────


@pytest.mark.asyncio
async def test_ingest_x_posts_fake_reader_rfc822_parses_epoch() -> None:
    raw = [
        {
            "id": "1",
            "handle": "x_alpha",
            "author": "x_alpha",
            "text": "$PEPE is pumping hard right now",
            "published": "Wed, 13 Aug 2026 10:15:00 GMT",
            "url": "https://x.com/x_alpha/status/1",
            "source": "x",
        }
    ]
    posts = await ingest_x_posts(lambda limit: raw)
    assert len(posts) == 1
    assert posts[0].author == "x_alpha"
    assert posts[0].ts == pytest.approx(EPOCH)


@pytest.mark.asyncio
async def test_ingest_x_posts_iso_still_works() -> None:
    raw = [{"handle": "x_alpha", "text": "$PEPE is a gem", "published": "2026-08-13T10:15:00Z"}]
    posts = await ingest_x_posts(lambda limit: raw)
    assert len(posts) == 1
    assert posts[0].ts == pytest.approx(EPOCH)


@pytest.mark.asyncio
async def test_ingest_x_posts_fails_open_raising_reader() -> None:
    def boom(limit: int) -> list[dict[str, object]]:
        raise RuntimeError("x reader down")

    assert await ingest_x_posts(boom) == []


# ── ingest_x_from_feed (real-reader wrapper) ────────────────────────────────


def _fake_feed_tweet() -> dict[str, object]:
    # Exact shape emitted by app.caching_shield.social_feed.get_twitter_feed
    return {
        "id": "abc123",
        "text": "$PEPE is a gem, buy now",
        "author": "Alice Crypto",
        "handle": "alice_x",
        "followers": "1.2M",
        "pfp": "https://unavatar.io/twitter/alice_x",
        "url": "https://x.com/alice_x/status/1",
        "published": "Wed, 13 Aug 2026 10:15:00 GMT",
        "source": "x",
        "type": "tweet",
    }


@pytest.mark.asyncio
async def test_ingest_x_from_feed_maps_real_reader_shape(monkeypatch) -> None:
    async def fake_get_twitter_feed(limit: int = 30) -> dict[str, object]:
        return {"tweets": [_fake_feed_tweet()], "accounts_monitored": 1}

    monkeypatch.setattr("app.caching_shield.social_feed.get_twitter_feed", fake_get_twitter_feed)
    posts = await ingest_x_from_feed(limit=10)
    assert len(posts) == 1
    assert posts[0].author == "alice_x"
    assert posts[0].source == "x"
    assert posts[0].url == "https://x.com/alice_x/status/1"
    assert posts[0].ts == pytest.approx(EPOCH)


@pytest.mark.asyncio
async def test_ingest_x_from_feed_fails_open_on_raising_reader(monkeypatch) -> None:
    async def boom(limit: int = 30) -> dict[str, object]:
        raise RuntimeError("feed down")

    monkeypatch.setattr("app.caching_shield.social_feed.get_twitter_feed", boom)
    assert await ingest_x_from_feed(limit=10) == []
