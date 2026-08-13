"""Pure unit tests for the coordinated-shill alert service.

Covers: ``ShillPost`` bridging (Telegram channel messages + X/Farcaster post
dicts), ``to_detector_post`` shape, ``group_by_author`` normalization,
``run_shill_detection`` emitting canonical syndicate alerts (3 KOLs → high,
5 KOLs → critical, known-scammer severity honored), the fail-open Telegram
ingestion path, and ``route`` channel mapping (rmialerts = high+,
main_channel = critical-only). No network, no DB.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domains.alerts.models import PlatformAlert
from app.domains.news_ingest.models import ChannelMessage
from app.domains.shill_alert import (
    ShillPost,
    group_by_author,
    ingest_telegram_posts,
    route,
    run_shill_detection,
)

TOKEN = "PEPE"
BASE_TS = 1_750_000_000.0  # deterministic epoch for window math


def _bullish_post(author: str, ts: float, source: str = "x", url: str | None = None) -> ShillPost:
    return ShillPost(
        author=author,
        text=f"${TOKEN} is a gem, buy now",
        ts=ts,
        source=source,
        url=url,
    )


# ── ShillPost bridging ──────────────────────────────────────────────────────


def test_from_channel_message_maps_channel_username() -> None:
    msg = ChannelMessage(
        channel="Shill Alpha",
        channel_username="shillalpha",
        message_id=42,
        text="$PEPE is a gem, buy now",
        posted_at=datetime(2026, 8, 13, 10, 0, tzinfo=UTC),
    )
    post = ShillPost.from_channel_message(msg)
    assert post.author == "shillalpha"
    assert post.source == "telegram"
    assert post.channel == "shillalpha"
    assert post.url == "https://t.me/shillalpha/42"
    assert post.ts == pytest.approx(datetime(2026, 8, 13, 10, 0, tzinfo=UTC).timestamp())


def test_from_channel_message_naive_datetime_assumed_utc() -> None:
    msg = ChannelMessage(
        channel="c", channel_username="chan", message_id=1, text="x", posted_at=datetime(2026, 8, 13, 10, 0)
    )
    post = ShillPost.from_channel_message(msg)
    assert post.ts == pytest.approx(datetime(2026, 8, 13, 10, 0, tzinfo=UTC).timestamp())


def test_from_social_post_x_flat_shape() -> None:
    raw = {
        "handle": "@alice_x",
        "text": "$PEPE is a gem, buy now",
        "created_at": "2026-08-13T10:00:00+00:00",
        "url": "https://x.com/alice_x/status/1",
    }
    post = ShillPost.from_social_post(raw, "x")
    assert post.author == "@alice_x"
    assert post.source == "x"
    assert post.url == "https://x.com/alice_x/status/1"
    assert post.ts == pytest.approx(datetime(2026, 8, 13, 10, 0, tzinfo=UTC).timestamp())


def test_from_social_post_farcaster_nested_author() -> None:
    raw = {
        "author": {"username": "bob_fc"},
        "text": "$PEPE is a gem",
        "timestamp": BASE_TS,
    }
    post = ShillPost.from_social_post(raw, "farcaster")
    assert post.author == "bob_fc"
    assert post.source == "farcaster"
    assert post.ts == BASE_TS


def test_from_social_post_missing_fields_defaults() -> None:
    post = ShillPost.from_social_post({"id": "1"}, "x")
    assert post.author == "unknown"
    assert post.text == ""
    assert post.ts == 0.0
    assert post.url is None


def test_to_detector_post_shape() -> None:
    post = _bullish_post("alice", BASE_TS, url="https://x.com/alice/1")
    d = post.to_detector_post()
    assert set(d) == {"author", "text", "ts", "url"}
    assert d["author"] == "alice"
    assert d["text"].startswith("$PEPE")
    assert d["ts"] == BASE_TS
    assert "source" not in d  # avoids _post_author fallback collision


def test_shill_post_rejects_unknown_source() -> None:
    with pytest.raises(ValueError, match="invalid source"):
        ShillPost(author="a", text="x", ts=0.0, source="tiktok")


# ── Grouping ────────────────────────────────────────────────────────────────


def test_group_by_author_normalizes_handles() -> None:
    posts = [
        _bullish_post("@Alice", BASE_TS),
        _bullish_post("alice", BASE_TS + 60),
        _bullish_post("BOB", BASE_TS + 120),
    ]
    grouped = group_by_author(posts)
    assert set(grouped) == {"alice", "bob"}
    assert len(grouped["alice"]) == 2
    assert len(grouped["bob"]) == 1


# ── Detection ───────────────────────────────────────────────────────────────


def test_run_shill_detection_three_kols_high() -> None:
    posts = [
        _bullish_post("alice", BASE_TS, source="x", url="https://x.com/alice/1"),
        _bullish_post("bob", BASE_TS + 600, source="telegram"),
        _bullish_post("carol", BASE_TS + 1200, source="x"),
    ]
    alerts = run_shill_detection(posts)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.category == "syndicate"
    assert alert.source == "shill_detector"
    assert alert.severity == "high"  # 3 KOLs < 5
    assert alert.title == f"Coordinated shill: {TOKEN} pumped by 3 channels"
    assert "alice, bob, carol" in alert.body
    assert "sources: telegram, x" in alert.body
    assert alert.urls == ["https://x.com/alice/1"]
    assert alert.event_key  # auto-derived by the canonical ingestion helper


def test_run_shill_detection_five_kols_critical() -> None:
    posts = [
        _bullish_post("alice", BASE_TS),
        _bullish_post("bob", BASE_TS + 300),
        _bullish_post("carol", BASE_TS + 600),
        _bullish_post("dave", BASE_TS + 900),
        _bullish_post("erin", BASE_TS + 1200),
    ]
    alerts = run_shill_detection(posts)
    assert len(alerts) == 1
    assert alerts[0].severity == "critical"  # ≥5 KOLs → critical


def test_run_shill_detection_no_cluster_below_threshold() -> None:
    posts = [_bullish_post("alice", BASE_TS), _bullish_post("bob", BASE_TS + 600)]
    assert run_shill_detection(posts) == []


def test_run_shill_detection_known_scammer_honors_dict_severity() -> None:
    posts = [_bullish_post("alice", BASE_TS)]
    alerts = run_shill_detection(posts, known_scammers={"alice"})
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.severity == "critical"  # detector's own severity honored
    assert alert.title == f"Known scammer shilling: {TOKEN}"
    assert alert.category == "syndicate"


# ── Telegram ingestion ──────────────────────────────────────────────────────


class _FakeClient:
    """Injected client exposing the async ``read_messages`` protocol."""

    def __init__(
        self, messages_by_channel: dict[str, list[ChannelMessage]] | None = None, *, fail: bool = False
    ) -> None:
        self._messages = messages_by_channel or {}
        self._fail = fail

    async def read_messages(self, channel_username: str, limit: int) -> list[ChannelMessage]:
        if self._fail:
            raise RuntimeError("telegram down")
        return self._messages.get(channel_username, [])[:limit]


@pytest.mark.asyncio
async def test_ingest_telegram_posts_maps_token_mentions_only() -> None:
    client = _FakeClient(
        {
            "shillalpha": [
                ChannelMessage(
                    channel="Shill Alpha",
                    channel_username="shillalpha",
                    message_id=1,
                    text="$PEPE is a gem, buy now",
                    posted_at=datetime(2026, 8, 13, 10, 0, tzinfo=UTC),
                ),
                ChannelMessage(
                    channel="Shill Alpha",
                    channel_username="shillalpha",
                    message_id=2,
                    text="no entities here",
                    posted_at=datetime(2026, 8, 13, 10, 1, tzinfo=UTC),
                ),
            ]
        }
    )
    posts = await ingest_telegram_posts(client, [{"username": "@shillalpha", "kind": "scammer_caller"}])
    assert len(posts) == 1
    assert posts[0].author == "shillalpha"
    assert posts[0].source == "telegram"
    assert posts[0].url == "https://t.me/shillalpha/1"


@pytest.mark.asyncio
async def test_ingest_telegram_posts_fail_open() -> None:
    posts = await ingest_telegram_posts(_FakeClient(fail=True), [{"username": "boom"}])
    assert posts == []


# ── Routing ─────────────────────────────────────────────────────────────────


def test_route_rmialerts_high_plus_main_critical_only() -> None:
    high = PlatformAlert(
        severity="high", category="syndicate", title="Coordinated shill: PEPE", source="shill_detector"
    )
    critical = PlatformAlert(
        severity="critical", category="syndicate", title="Coordinated shill: TOKEN", source="shill_detector"
    )
    plan = route([high, critical])
    assert plan["counts"]["rmialerts"] == 2  # high + critical
    assert plan["counts"]["main_channel"] == 1  # critical only
    assert len(plan["channels"]["rmialerts"]) == 2
    assert len(plan["channels"]["main_channel"]) == 1
    assert plan["channels"]["main_channel"][0]["severity"] == "critical"
    assert isinstance(plan["plan"], list)
    assert all(entry["send"] is not None for entry in plan["plan"])
