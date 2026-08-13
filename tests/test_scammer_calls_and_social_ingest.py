"""PURE tests: CallStore, /api/v1/kol/scammer-calls, X/Farcaster ingestion.

No network, no DB — file I/O is confined to pytest's tmp_path. The
scammer-calls endpoint handler is invoked directly (no TestClient / app
context) against a tmp_path-backed store injected via monkeypatch, and the
social readers are duck-typed fakes (list-returning, wrapping or raising).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.domains.shill_alert.call_store import CallStore
from app.domains.shill_alert.service import ingest_farcaster_posts, ingest_x_posts
from app.routers import kol_intel


def _call(handle: str = "scammer1", severity: str = "high", **overrides: object) -> dict[str, object]:
    """A scammer-call record matching the frontend contract keys."""
    record: dict[str, object] = {
        "id": f"call-{handle}",
        "handle": handle,
        "channel": None,
        "post_text": "GM! $PEPE is going to the moon",
        "token_symbol": "PEPE",
        "token_address": "0x" + "a" * 40,
        "chain": "ethereum",
        "severity": severity,
        "captured_at": "2026-08-13T00:00:00Z",
        "source_url": f"https://x.com/{handle}/status/1",
    }
    record.update(overrides)
    return record


def _farcaster_cast(handle: str = "fc_alpha", text: str = "$PEPE listing imminent") -> dict[str, object]:
    return {
        "author": {"username": handle},
        "text": text,
        "timestamp": "2026-08-13T10:00:00Z",
        "url": f"https://warpcast.com/{handle}/0xabc",
    }


def _x_post(handle: str = "x_alpha", text: str = "$PEPE is pumping hard right now") -> dict[str, object]:
    return {
        "id": "1",
        "handle": handle,
        "author": handle,
        "text": text,
        "published": "2026-08-13T10:00:00+00:00",
        "url": f"https://x.com/{handle}/status/1",
        "source": "x",
    }


# ── CallStore ──────────────────────────────────────────────────────────────


def test_call_store_add_list_roundtrip(tmp_path) -> None:
    store = CallStore(path=tmp_path / "calls.json")
    assert store.add(_call()) is True
    assert store.add(_call(handle="scammer2", severity="critical")) is True
    calls = store.list()
    assert len(calls) == 2
    assert calls[0]["handle"] == "scammer2"  # newest first
    assert calls[0]["severity"] == "critical"
    assert calls[1]["handle"] == "scammer1"


def test_call_store_list_limit(tmp_path) -> None:
    store = CallStore(path=tmp_path / "calls.json")
    for i in range(10):
        store.add(_call(handle=f"scammer{i}", severity="low"))
    calls = store.list(limit=3)
    assert [c["handle"] for c in calls] == ["scammer9", "scammer8", "scammer7"]


def test_call_store_severity_filter_is_case_insensitive(tmp_path) -> None:
    store = CallStore(path=tmp_path / "calls.json")
    store.add(_call(handle="a", severity="high"))
    store.add(_call(handle="b", severity="critical"))
    store.add(_call(handle="c", severity="low"))
    assert [c["handle"] for c in store.list(severity="HIGH")] == ["a"]
    assert [c["handle"] for c in store.list(severity="critical")] == ["b"]
    assert [c["handle"] for c in store.list(severity="nope")] == []


def test_call_store_persistence_across_instances(tmp_path) -> None:
    path = tmp_path / "calls.json"
    CallStore(path=path).add(_call())
    store2 = CallStore(path=path)  # fresh instance reads the same file
    assert len(store2.list()) == 1
    assert store2.list()[0]["handle"] == "scammer1"


def test_call_store_empty_to_dict(tmp_path) -> None:
    assert CallStore(path=tmp_path / "missing.json").to_dict() == {"calls": [], "total": 0}


def test_call_store_corrupt_lines_skipped(tmp_path) -> None:
    path = tmp_path / "calls.json"
    path.write_text(
        '{"handle": "ok1", "severity": "high"}\nnot-json\n{"handle": "ok2", "severity": "low"}\n',
        encoding="utf-8",
    )
    assert [c["handle"] for c in CallStore(path=path).list()] == ["ok2", "ok1"]


# ── Endpoint (handler called directly, store injected) ────────────────────


def test_scammer_calls_endpoint_empty_shape(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(kol_intel, "_CALL_STORE", CallStore(path=tmp_path / "empty.json"))
    assert kol_intel.scammer_calls(limit=50, severity="") == {"calls": [], "total": 0}


def test_scammer_calls_endpoint_populated_shape(tmp_path, monkeypatch) -> None:
    store = CallStore(path=tmp_path / "calls.json")
    store.add(_call())
    store.add(_call(handle="scammer2", severity="critical"))
    monkeypatch.setattr(kol_intel, "_CALL_STORE", store)

    body = kol_intel.scammer_calls(limit=50, severity="")
    assert body["total"] == 2
    assert len(body["calls"]) == 2
    for key in (
        "id",
        "handle",
        "channel",
        "post_text",
        "token_symbol",
        "token_address",
        "chain",
        "severity",
        "captured_at",
        "source_url",
    ):
        assert key in body["calls"][0]

    crit = kol_intel.scammer_calls(limit=50, severity="critical")
    assert crit["total"] == 1
    assert crit["calls"][0]["handle"] == "scammer2"


def test_scammer_calls_endpoint_respects_limit(tmp_path, monkeypatch) -> None:
    store = CallStore(path=tmp_path / "calls.json")
    for i in range(5):
        store.add(_call(handle=f"s{i}", severity="low"))
    monkeypatch.setattr(kol_intel, "_CALL_STORE", store)
    body = kol_intel.scammer_calls(limit=2, severity="")
    assert body["total"] == 2
    assert [c["handle"] for c in body["calls"]] == ["s4", "s3"]


# ── X / Farcaster ingestion ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_farcaster_posts_maps_casts() -> None:
    posts = await ingest_farcaster_posts(lambda limit: [_farcaster_cast()])
    assert len(posts) == 1
    post = posts[0]
    assert post.author == "fc_alpha"
    assert post.source == "farcaster"
    assert post.url == "https://warpcast.com/fc_alpha/0xabc"
    assert post.ts == pytest.approx(datetime(2026, 8, 13, 10, 0, tzinfo=UTC).timestamp())
    assert "$PEPE" in post.text


@pytest.mark.asyncio
async def test_ingest_x_posts_maps_posts() -> None:
    posts = await ingest_x_posts(lambda limit: [_x_post()])
    assert len(posts) == 1
    post = posts[0]
    assert post.author == "x_alpha"
    assert post.source == "x"
    assert post.url == "https://x.com/x_alpha/status/1"
    assert post.ts == pytest.approx(datetime(2026, 8, 13, 10, 0, tzinfo=UTC).timestamp())


@pytest.mark.asyncio
async def test_ingest_x_posts_accepts_real_reader_wrapped_shape() -> None:
    # app.caching_shield.social_feed.get_twitter_feed returns {"tweets": [...]}
    raw = {"tweets": [_x_post()], "accounts_monitored": 1}
    posts = await ingest_x_posts(lambda limit: raw)
    assert len(posts) == 1
    assert posts[0].author == "x_alpha"


@pytest.mark.asyncio
async def test_ingest_farcaster_posts_accepts_wrapped_shape() -> None:
    posts = await ingest_farcaster_posts(lambda limit: {"casts": [_farcaster_cast()]})
    assert len(posts) == 1
    assert posts[0].author == "fc_alpha"


@pytest.mark.asyncio
async def test_ingest_x_posts_drops_non_token_posts() -> None:
    posts = await ingest_x_posts(lambda limit: [_x_post(text="just vibes, no ticker here")])
    assert posts == []


@pytest.mark.asyncio
async def test_ingest_reader_limit_is_forwarded() -> None:
    seen: list[int] = []

    def reader(limit: int) -> list[dict[str, object]]:
        seen.append(limit)
        return [_x_post()]

    await ingest_x_posts(reader, limit=7)
    assert seen == [7]


@pytest.mark.asyncio
async def test_ingest_x_posts_fails_open_empty_reader() -> None:
    assert await ingest_x_posts(lambda limit: []) == []


@pytest.mark.asyncio
async def test_ingest_farcaster_posts_fails_open_empty_reader() -> None:
    assert await ingest_farcaster_posts(lambda limit: []) == []


@pytest.mark.asyncio
async def test_ingest_x_posts_fails_open_raising_reader() -> None:
    def boom(limit: int) -> list[dict[str, object]]:
        raise RuntimeError("x reader down")

    assert await ingest_x_posts(boom) == []


@pytest.mark.asyncio
async def test_ingest_farcaster_posts_fails_open_raising_reader() -> None:
    async def boom(limit: int) -> list[dict[str, object]]:
        raise RuntimeError("farcaster reader down")

    assert await ingest_farcaster_posts(boom) == []


@pytest.mark.asyncio
async def test_ingest_x_posts_fails_open_garbage_reader() -> None:
    assert await ingest_x_posts(lambda limit: None) == []
    assert await ingest_x_posts(lambda limit: "nope") == []
