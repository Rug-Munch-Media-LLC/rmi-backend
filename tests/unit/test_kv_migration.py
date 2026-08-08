"""Unit tests for the KV Migration API (``app.api.v1.kv_migration``).

Exercises the DegenFeed KV-replacement endpoints through a minimal FastAPI
app (router only) via TestClient:

  Redis-backed (real flow through fakeredis):
    1. rate-limits report + check round-trip
    2. rate-limits check with no record (count=0, blocked=False)
    3. queue enqueue + dequeue round-trip
    4. queue dequeue on an empty queue ([])
    5. sessions upsert + get + delete round-trip
    6. sessions get for an unknown/expired id (empty data)

  Postgres-backed (session mocked — no live DB in unit env):
    7. chat-history POST persists a message
    8. chat-history GET returns messages oldest-first
    9. identities POST upsert + GET round-trip
   10. identities GET returns 404 when unknown
   11. bookmarks POST is idempotent (duplicate does not re-add)
   12. bookmarks GET lists bookmarks for an address

  Auth:
   13. every endpoint rejects requests without a valid X-API-Key (401)

No real Redis or Postgres is touched; fakeredis stands in for Redis and an
AsyncMock session stands in for SQLAlchemy.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "a" * 32)
os.environ.setdefault("ENVIRONMENT", "dev")

from app.api.v1.kv_migration import router as kv_router
from app.core.dependencies import get_db

TEST_TOKEN = "test-kv-token-1234567890"
AUTH = {"X-API-Key": TEST_TOKEN}


@pytest.fixture
def app() -> FastAPI:
    """Minimal app with just the KV migration router."""
    application = FastAPI()
    application.include_router(kv_router)
    return application


@pytest.fixture
def redis_client():
    """fakeredis async client standing in for app.core.redis."""
    return fakeredis.aioredis.FakeRedis(decode_responses=False)


def _use_redis(app: FastAPI, redis_client: Any) -> None:
    """Point the router's _redis() helper at the fake client."""
    fake = AsyncMock(return_value=redis_client)
    patcher = patch("app.api.v1.kv_migration._redis", new=fake)
    patcher.start()
    app.state._redis_patcher = patcher


def _use_session(app: FastAPI, session: Any) -> None:
    """Override the get_db dependency with a fake session."""
    app.dependency_overrides[get_db] = lambda: session


def _redis_session(app: FastAPI, redis_client: Any, session: Any | None = None) -> None:
    """Activate fake Redis (+ optional fake session) for the app."""
    _use_redis(app, redis_client)
    if session is not None:
        _use_session(app, session)


@pytest.fixture(autouse=True)
def _auth_tokens():
    """Expose a known token to the router's auth check."""
    patcher = patch("app.api.v1.kv_migration._AUTH_TOKENS", {TEST_TOKEN})
    patcher.start()
    yield
    patcher.stop()


# ── Rate limits ────────────────────────────────────────────────────────


def test_rate_limit_report_and_check(app: FastAPI, redis_client: Any) -> None:
    """Report stores a count/max pair; check reads it back and blocks at max."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/rate-limits/report",
            json={"ip": "1.2.3.4", "path": "/api/feed", "count": 19, "max": 20, "window_ms": 5000},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

        status = client.get(
            "/api/v1/rate-limits/check",
            params={"ip": "1.2.3.4", "path": "/api/feed"},
            headers=AUTH,
        )
        assert status.status_code == 200
        assert status.json() == {"count": 19, "blocked": False}

        # Bump over the limit → blocked
        client.post(
            "/api/v1/rate-limits/report",
            json={"ip": "1.2.3.4", "path": "/api/feed", "count": 20, "max": 20, "window_ms": 5000},
            headers=AUTH,
        )
        status = client.get(
            "/api/v1/rate-limits/check",
            params={"ip": "1.2.3.4", "path": "/api/feed"},
            headers=AUTH,
        )
        assert status.json() == {"count": 20, "blocked": True}


def test_rate_limit_check_missing_record(app: FastAPI, redis_client: Any) -> None:
    """No record → count 0, not blocked."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        status = client.get(
            "/api/v1/rate-limits/check",
            params={"ip": "9.9.9.9", "path": "/nope"},
            headers=AUTH,
        )
        assert status.status_code == 200
        assert status.json() == {"count": 0, "blocked": False}


def test_rate_limit_batch_sync_and_check(app: FastAPI, redis_client: Any) -> None:
    """Batch endpoint stores entries; check reads them back individually."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/rate-limits/batch",
            json={
                "source": "degenfeed-worker",
                "entries": [
                    {"ip": "1.2.3.4", "path": "/api/feed", "count": 15, "max": 20, "window_ms": 5000},
                    {"ip": "5.6.7.8", "path": "/api/stats", "count": 30, "max": 30, "window_ms": 10000},
                ],
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["accepted"] == 2

        # Verify each entry is individually readable
        s1 = client.get(
            "/api/v1/rate-limits/check",
            params={"ip": "1.2.3.4", "path": "/api/feed"},
            headers=AUTH,
        )
        assert s1.json() == {"count": 15, "blocked": False}

        s2 = client.get(
            "/api/v1/rate-limits/check",
            params={"ip": "5.6.7.8", "path": "/api/stats"},
            headers=AUTH,
        )
        assert s2.json() == {"count": 30, "blocked": True}


def test_rate_limit_batch_default_values(app: FastAPI, redis_client: Any) -> None:
    """Batch accepts entries without explicit source/window_ms defaults."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/rate-limits/batch",
            json={
                "entries": [
                    {"ip": "203.0.113.1", "path": "/api/x", "count": 1, "max": 5},
                ],
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.json()["accepted"] == 1


# ── Queue operations ───────────────────────────────────────────────────


def test_queue_enqueue_dequeue_round_trip(app: FastAPI, redis_client: Any) -> None:
    """Enqueue two jobs, dequeue them FIFO with payload intact."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        r1 = client.post(
            "/api/v1/queue/enqueue",
            json={"queue": "sync", "key": "k1", "payload": {"a": 1}},
            headers=AUTH,
        )
        assert r1.status_code == 200
        job_id_1 = r1.json()["id"]

        r2 = client.post(
            "/api/v1/queue/enqueue",
            json={"queue": "sync", "key": "k2", "payload": {"b": 2}},
            headers=AUTH,
        )
        assert r2.status_code == 200
        job_id_2 = r2.json()["id"]

        deq = client.get("/api/v1/queue/dequeue", params={"queue": "sync", "limit": 10}, headers=AUTH)
        assert deq.status_code == 200
        items = deq.json()["items"]
        assert [i["key"] for i in items] == ["k1", "k2"]
        assert items[0]["id"] == job_id_1
        assert items[1]["id"] == job_id_2
        assert items[0]["payload"] == {"a": 1}

        # Queue is now drained
        deq2 = client.get("/api/v1/queue/dequeue", params={"queue": "sync", "limit": 10}, headers=AUTH)
        assert deq2.json()["items"] == []


def test_queue_rejects_unknown_queue(app: FastAPI, redis_client: Any) -> None:
    """Literal queue validation returns 422 for a disallowed queue name."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/queue/enqueue",
            json={"queue": "bogus", "key": "k", "payload": {}},
            headers=AUTH,
        )
        assert resp.status_code == 422


def test_queue_accepts_new_queue_names(app: FastAPI, redis_client: Any) -> None:
    """Enqueue accepts the expanded queue name set (scheduled, bridgy:*)."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        for queue in ("scheduled", "bridgy:users", "bridgy:config"):
            resp = client.post(
                "/api/v1/queue/enqueue",
                json={"queue": queue, "key": "k", "payload": {"x": 1}},
                headers=AUTH,
            )
            assert resp.status_code == 200, f"queue={queue} should be accepted"
            assert resp.json()["ok"] is True
            assert resp.json()["id"]

            # Dequeue from the new queue should also work
            deq = client.get(
                "/api/v1/queue/dequeue",
                params={"queue": queue, "limit": 1},
                headers=AUTH,
            )
            assert deq.status_code == 200, f"dequeue {queue} should be accepted"
            assert len(deq.json()["items"]) == 1


# ── Sessions ───────────────────────────────────────────────────────────


def test_sessions_upsert_get_delete(app: FastAPI, redis_client: Any) -> None:
    """Session is stored with TTL, read back, then deleted."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions",
            json={"session_id": "sess-1", "data": {"user": "alice"}, "ttl": 3600},
            headers=AUTH,
        )
        assert created.status_code == 200
        assert created.json()["session_id"] == "sess-1"
        assert created.json()["data"] == {"user": "alice"}

        fetched = client.get("/api/v1/sessions", params={"session_id": "sess-1"}, headers=AUTH)
        assert fetched.status_code == 200
        assert fetched.json()["data"] == {"user": "alice"}
        assert fetched.json()["ttl"] == 3600

        deleted = client.delete("/api/v1/sessions", params={"session_id": "sess-1"}, headers=AUTH)
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True}

        gone = client.get("/api/v1/sessions", params={"session_id": "sess-1"}, headers=AUTH)
        assert gone.status_code == 200
        assert gone.json()["data"] == {}


def test_session_generated_id(app: FastAPI, redis_client: Any) -> None:
    """Omitting session_id generates one server-side."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/sessions",
            json={"data": {"fresh": True}},
            headers=AUTH,
        )
        assert created.status_code == 200
        assert created.json()["session_id"]


# ── Chat history (Postgres — mocked session) ───────────────────────────


def _chat_message(**overrides: Any) -> Any:
    """Build a ChatMessage-like ORM object without a live DB."""
    from app.models.kv_migration import ChatMessage

    msg = ChatMessage(
        room_id=overrides.get("room_id", "room-1"),
        sender=overrides.get("sender", "alice"),
        content=overrides.get("content", "hello"),
    )
    msg.id = overrides.get("id", uuid.UUID(int=1))
    msg.created_at = overrides.get("created_at", datetime.now(UTC))
    msg.updated_at = msg.created_at
    return msg


def test_chat_history_create(app: FastAPI) -> None:
    """POST /chat-history adds a message and returns it."""
    session = AsyncMock()
    session.add = MagicMock()  # db.add is sync in real SQLAlchemy

    async def _refresh(obj: Any) -> None:
        obj.id = uuid.UUID(int=42)
        obj.created_at = datetime.now(UTC)

    session.refresh = AsyncMock(side_effect=_refresh)
    _redis_session(app, fakeredis.aioredis.FakeRedis(), session=session)
    with patch("app.api.v1.kv_migration._ensure_kv_tables", AsyncMock()), TestClient(app) as client:
        resp = client.post(
            "/api/v1/chat-history",
            json={"room_id": "room-1", "sender": "alice", "content": "gm"},
            headers=AUTH,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["room_id"] == "room-1"
    assert body["sender"] == "alice"
    assert body["content"] == "gm"
    assert body["id"] == str(uuid.UUID(int=42))
    assert session.add.call_count == 1


def test_chat_history_list_oldest_first(app: FastAPI) -> None:
    """GET /chat-history returns stored messages newest-first query, oldest-first output."""
    session = AsyncMock()
    older = _chat_message(content="first", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    newer = _chat_message(content="second", created_at=datetime(2026, 1, 2, tzinfo=UTC))
    result = MagicMock()
    result.scalars.return_value.all.return_value = [newer, older]
    session.execute = AsyncMock(return_value=result)
    _redis_session(app, fakeredis.aioredis.FakeRedis(), session=session)
    with patch("app.api.v1.kv_migration._ensure_kv_tables", AsyncMock()), TestClient(app) as client:
        resp = client.get(
            "/api/v1/chat-history",
            params={"room_id": "room-1"},
            headers=AUTH,
        )
    assert resp.status_code == 200
    contents = [i["content"] for i in resp.json()["items"]]
    assert contents == ["first", "second"]


# ── Identities (Postgres — mocked session) ─────────────────────────────


def test_identity_upsert_and_get(app: FastAPI) -> None:
    """POST then GET /identities returns the stored mapping."""
    session = AsyncMock()
    session.add = MagicMock()  # db.add is sync in real SQLAlchemy

    # First select (upsert) → no existing row; second select (get) → returns row
    from app.models.kv_migration import IdentityMapping

    async def _refresh(obj: Any) -> None:
        obj.created_at = datetime(2026, 1, 1, tzinfo=UTC)
        obj.updated_at = datetime(2026, 1, 1, tzinfo=UTC)

    session.refresh = AsyncMock(side_effect=_refresh)

    mapping = IdentityMapping(address="0xabc", data={"tag": "vip"})
    mapping.updated_at = datetime(2026, 1, 1, tzinfo=UTC)

    no_row = MagicMock()
    no_row.scalar_one_or_none.return_value = None
    with_row = MagicMock()
    with_row.scalar_one_or_none.return_value = mapping

    session.execute = AsyncMock(side_effect=[no_row, with_row])
    _redis_session(app, fakeredis.aioredis.FakeRedis(), session=session)
    with patch("app.api.v1.kv_migration._ensure_kv_tables", AsyncMock()), TestClient(app) as client:
        put = client.post(
            "/api/v1/identities",
            json={"address": "0xabc", "data": {"tag": "vip"}},
            headers=AUTH,
        )
        got = client.get("/api/v1/identities", params={"address": "0xabc"}, headers=AUTH)

    assert put.status_code == 200
    assert put.json()["address"] == "0xabc"
    assert put.json()["data"] == {"tag": "vip"}
    assert got.status_code == 200
    assert got.json()["address"] == "0xabc"
    assert got.json()["data"] == {"tag": "vip"}


def test_identity_get_unknown_returns_404(app: FastAPI) -> None:
    """GET /identities for an unknown address returns 404."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=result)
    _redis_session(app, fakeredis.aioredis.FakeRedis(), session=session)
    with patch("app.api.v1.kv_migration._ensure_kv_tables", AsyncMock()), TestClient(app) as client:
        resp = client.get("/api/v1/identities", params={"address": "0xunknown"}, headers=AUTH)
    assert resp.status_code == 404


# ── Bookmarks (Postgres — mocked session) ──────────────────────────────


def test_bookmark_create_idempotent_and_list(app: FastAPI) -> None:
    """POST /bookmarks is idempotent; GET lists stored bookmarks."""
    session = AsyncMock()
    session.add = MagicMock()  # db.add is sync in real SQLAlchemy
    no_row = MagicMock()
    no_row.scalar_one_or_none.return_value = None

    from app.models.kv_migration import Bookmark

    b1 = Bookmark(address="0xabc", post_id="p1")
    b1.id = uuid.UUID(int=7)
    b1.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    b2 = Bookmark(address="0xabc", post_id="p2")
    b2.id = uuid.UUID(int=8)
    b2.created_at = datetime(2026, 1, 2, tzinfo=UTC)

    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = [b2, b1]

    # first POST → no existing row (add), second POST → existing row (no add)
    existing_row = MagicMock()
    existing_row.scalar_one_or_none.return_value = b1
    session.execute = AsyncMock(side_effect=[no_row, existing_row, list_result])
    _redis_session(app, fakeredis.aioredis.FakeRedis(), session=session)
    with patch("app.api.v1.kv_migration._ensure_kv_tables", AsyncMock()), TestClient(app) as client:
        first = client.post(
            "/api/v1/bookmarks",
            json={"address": "0xabc", "post_id": "p1"},
            headers=AUTH,
        )
        second = client.post(
            "/api/v1/bookmarks",
            json={"address": "0xabc", "post_id": "p1"},
            headers=AUTH,
        )
        listed = client.get(
            "/api/v1/bookmarks",
            params={"address": "0xabc"},
            headers=AUTH,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"ok": True}
    # added exactly once — the duplicate POST did not re-add
    add_calls = [c for c in session.add.call_args_list if c.args and isinstance(c.args[0], Bookmark)]
    assert len(add_calls) == 1
    assert listed.status_code == 200
    assert [i["post_id"] for i in listed.json()["items"]] == ["p2", "p1"]


# ── Auth ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        (
            "post",
            "/api/v1/rate-limits/report",
            {"json": {"ip": "1.1.1.1", "path": "/x", "count": 1, "max": 2, "window_ms": 1000}},
        ),
        ("get", "/api/v1/rate-limits/check", {"params": {"ip": "1.1.1.1", "path": "/x"}}),
        ("post", "/api/v1/queue/enqueue", {"json": {"queue": "sync", "key": "k", "payload": {}}}),
        ("get", "/api/v1/queue/dequeue", {"params": {"queue": "sync"}}),
        ("post", "/api/v1/sessions", {"json": {"data": {}}}),
        ("get", "/api/v1/sessions", {"params": {"session_id": "x"}}),
        ("delete", "/api/v1/sessions", {"params": {"session_id": "x"}}),
        ("post", "/api/v1/chat-history", {"json": {"room_id": "r", "sender": "s", "content": "c"}}),
        ("get", "/api/v1/chat-history", {"params": {"room_id": "r"}}),
        ("post", "/api/v1/identities", {"json": {"address": "a", "data": {}}}),
        ("get", "/api/v1/identities", {"params": {"address": "a"}}),
        ("post", "/api/v1/bookmarks", {"json": {"address": "a", "post_id": "p"}}),
        ("get", "/api/v1/bookmarks", {"params": {"address": "a"}}),
    ],
)
def test_all_endpoints_require_auth(app: FastAPI, method: str, path: str, kwargs: dict[str, Any]) -> None:
    """Every endpoint rejects requests without a valid X-API-Key."""
    _redis_session(app, fakeredis.aioredis.FakeRedis())
    with TestClient(app) as client:
        resp = getattr(client, method)(path, **kwargs)
        assert resp.status_code == 401


def test_invalid_key_rejected(app: FastAPI, redis_client: Any) -> None:
    """A wrong key is rejected even when Redis is up."""
    _redis_session(app, redis_client)
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/rate-limits/check",
            params={"ip": "1.1.1.1", "path": "/x"},
            headers={"X-API-Key": "wrong-token"},
        )
        assert resp.status_code == 401
