"""Tests for the bot admin API (app/routers/admin/bot_admin.py).

Covers:
  - Auth: missing key → 401, non-owner → 403, owner → 200
  - Bot control toggles (Redis + bot SQLite sync)
  - User management (list, detail, tier, ban, unban)
  - Analytics endpoints
  - Broadcast queueing

Redis is mocked at the module boundary (``admin_state.get_redis_async``) and
the bot SQLite DB is redirected to a temp file so the real bot_users.db is
never touched. Auth is exercised end-to-end through the app factory.
"""

from __future__ import annotations

import os

os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["ADMIN_API_KEY"] = "test-admin-key-32-chars-long!!!"
os.environ["JWT_SECRET"] = "a" * 32
os.environ["OWNER_CHAT_ID"] = "7075336557"

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.domains.telegram.rugmunchbot import db

OWNER_HEADERS = {
    "X-API-Key": "test-auth-token-32-chars-long!!",
    "X-User-ID": "7075336557",
}


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the bot DB at a temp file so tests never touch the real DB."""
    test_db = str(tmp_path / "bot_test.db")
    monkeypatch.setattr(db, "DB_PATH", test_db)
    db.init_db()
    # Seed a couple of users for list/detail/ban/tier tests.
    conn = db.get_db()
    now = int(__import__("time").time())
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, username, first_name, tier, total_scans, total_ai_msgs, created_at, last_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (7075336557, "owner", "Owner", "free", 5, 10, now - 86400, now - 3600),
    )
    conn.execute(
        "INSERT OR REPLACE INTO users (user_id, username, first_name, tier, total_scans, total_ai_msgs, created_at, last_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (111, "alice", "Alice", "pro", 50, 100, now - 172800, now - 60),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def mock_redis():
    """Patch admin_state Redis access with a fake async client."""
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=None)
    fake.set = AsyncMock()
    fake.incr = AsyncMock()
    fake.incrby = AsyncMock()
    fake.hincrby = AsyncMock()
    fake.sadd = AsyncMock()
    fake.srem = AsyncMock()
    fake.sismember = AsyncMock(return_value=False)
    fake.smembers = AsyncMock(return_value=set())
    fake.lpush = AsyncMock()
    fake.rpop = AsyncMock(return_value=None)
    fake.keys = AsyncMock(return_value=[])
    fake.hgetall = AsyncMock(return_value={})
    with patch("app.domains.telegram.rugmunchbot.admin_state.get_redis_async", return_value=fake):
        yield fake


@pytest.fixture(autouse=True)
def _allow_rate_limits():
    """Keep the global rate limiter patched open for the whole test.

    The unified rate limiter has a tiny admin burst (2) and a pre-existing
    bug when it rejects (int header values crash JSONResponse). Unit tests
    must not hit either, so every request is allowed.
    """
    from app.core.rate_limiter import UnifiedRateLimiter

    async def _allow(self, key, rate=1.0, burst=5.0, tokens=1.0):
        return (True, {"X-RateLimit-Limit": int(burst), "X-RateLimit-Remaining": 1, "X-RateLimit-Reset": 0})

    with patch.object(UnifiedRateLimiter, "acquire", _allow):
        yield


@pytest.fixture
def app(mock_redis):
    from app.factory import create_app

    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


# ══════════════════════════════════════════════
# Auth
# ══════════════════════════════════════════════


class TestBotAdminAuth:
    def test_missing_key_rejected(self, client: TestClient) -> None:
        resp = client.post("/api/v1/admin/bot/toggle", json={"enabled": True})
        assert resp.status_code in (401, 403)

    def test_wrong_key_rejected(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/admin/bot/toggle",
            json={"enabled": True},
            headers={"X-API-Key": "wrong-key", "X-User-ID": "7075336557"},
        )
        assert resp.status_code == 401

    def test_non_owner_forbidden(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/admin/bot/toggle",
            json={"enabled": True},
            headers={"X-API-Key": "test-auth-token-32-chars-long!!", "X-User-ID": "999"},
        )
        assert resp.status_code == 403

    def test_owner_allowed(self, client: TestClient) -> None:
        resp = client.post("/api/v1/admin/bot/toggle", json={"enabled": True}, headers=OWNER_HEADERS)
        assert resp.status_code == 200


# ══════════════════════════════════════════════
# Bot control
# ══════════════════════════════════════════════


class TestBotControl:
    def test_toggle(self, client: TestClient, mock_redis) -> None:
        resp = client.post("/api/v1/admin/bot/toggle", json={"enabled": False}, headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False
        mock_redis.set.assert_called()

    def test_toggle_chat(self, client: TestClient, mock_redis) -> None:
        resp = client.post("/api/v1/admin/bot/toggle-chat", json={"chat_enabled": False}, headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["chat_enabled"] is False

    def test_status_shape(self, client: TestClient) -> None:
        resp = client.get("/api/v1/admin/bot/status", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"enabled", "chat_enabled", "users", "active_today"}
        assert body["users"] == 2


# ══════════════════════════════════════════════
# User management
# ══════════════════════════════════════════════


class TestUserManagement:
    def test_list_users_pagination(self, client: TestClient) -> None:
        resp = client.get("/api/v1/admin/bot/users?per_page=1&page=2", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert len(body["users"]) == 1

    def test_list_users_tier_filter(self, client: TestClient) -> None:
        resp = client.get("/api/v1/admin/bot/users?tier=pro", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert [u["user_id"] for u in resp.json()["users"]] == [111]

    def test_list_users_invalid_tier(self, client: TestClient) -> None:
        resp = client.get("/api/v1/admin/bot/users?tier=bogus", headers=OWNER_HEADERS)
        assert resp.status_code == 400

    def test_user_detail(self, client: TestClient) -> None:
        resp = client.get("/api/v1/admin/bot/users/111", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["user_id"] == 111
        assert body["tier"] == "pro"
        assert body["total_scans"] == 50
        assert body["total_ai_msgs"] == 100
        assert body["is_banned"] is False

    def test_user_detail_not_found(self, client: TestClient) -> None:
        resp = client.get("/api/v1/admin/bot/users/999999", headers=OWNER_HEADERS)
        assert resp.status_code == 404

    def test_set_tier(self, client: TestClient) -> None:
        resp = client.post("/api/v1/admin/bot/users/7075336557/tier", json={"tier": "hunter"}, headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["tier"] == "hunter"
        assert db.get_user_tier(7075336557) == "hunter"

    def test_set_tier_invalid(self, client: TestClient) -> None:
        resp = client.post("/api/v1/admin/bot/users/7075336557/tier", json={"tier": "ultra"}, headers=OWNER_HEADERS)
        assert resp.status_code == 400

    def test_set_tier_user_not_found(self, client: TestClient) -> None:
        resp = client.post("/api/v1/admin/bot/users/999999/tier", json={"tier": "pro"}, headers=OWNER_HEADERS)
        assert resp.status_code == 404

    def test_ban_updates_sqlite(self, client: TestClient, mock_redis) -> None:
        resp = client.post("/api/v1/admin/bot/users/111/ban", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["is_banned"] is True
        mock_redis.sadd.assert_called()

    def test_ban_user_not_found(self, client: TestClient) -> None:
        resp = client.post("/api/v1/admin/bot/users/999999/ban", headers=OWNER_HEADERS)
        assert resp.status_code == 404

    def test_unban_updates_sqlite(self, client: TestClient, mock_redis) -> None:
        resp = client.post("/api/v1/admin/bot/users/111/unban", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["is_banned"] is False
        mock_redis.srem.assert_called()


# ══════════════════════════════════════════════
# Analytics
# ══════════════════════════════════════════════


class TestAnalytics:
    def test_daily_shape(self, client: TestClient) -> None:
        resp = client.get("/api/v1/admin/bot/analytics/daily?days=2", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        days = resp.json()["days"]
        assert len(days) == 2
        assert set(days[0]) >= {"date", "messages", "ai_queries", "new_users", "tokens"}

    def test_usage_shape(self, client: TestClient) -> None:
        resp = client.get("/api/v1/admin/bot/analytics/usage", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"tier_breakdown", "top_users", "popular_commands", "banned_count"}
        tiers = {t["tier"] for t in body["tier_breakdown"]}
        assert "pro" in tiers


# ══════════════════════════════════════════════
# Broadcast
# ══════════════════════════════════════════════


class TestBroadcast:
    def test_broadcast_queued(self, client: TestClient, mock_redis) -> None:
        resp = client.post(
            "/api/v1/admin/bot/broadcast",
            json={"message": "Hello <b>world</b>"},
            headers=OWNER_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "queued"
        assert body["broadcast_id"]
        mock_redis.lpush.assert_called()

    def test_broadcast_invalid_body(self, client: TestClient) -> None:
        resp = client.post("/api/v1/admin/bot/broadcast", json={"message": ""}, headers=OWNER_HEADERS)
        assert resp.status_code == 422

    def test_broadcast_history(self, client: TestClient, mock_redis) -> None:
        mock_redis.keys = AsyncMock(return_value=[])
        resp = client.get("/api/v1/admin/bot/broadcasts", headers=OWNER_HEADERS)
        assert resp.status_code == 200
        assert "broadcasts" in resp.json()
