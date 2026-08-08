"""Unit tests for the central rugmunchbot ban gate (bot.install_ban_gate).

Contract under test:
  - banned users are blocked at EVERY entry point (message, callback, inline)
    before any handler runs — the wrapped process_update is never called
  - banned users receive the single "You are banned" notice
  - bans from BOTH stores are enforced: local SQLite users.is_banned and the
    Redis set bot:admin:banned_users (admin_state.is_user_banned)
  - owners are exempt and always pass through
  - non-banned users pass through untouched

Redis is mocked at the module boundary (admin_state.is_user_banned) and the
bot SQLite DB is redirected to a temp file so the real bot_users.db is never
touched.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from app.domains.telegram.rugmunchbot import admin_state, bot, db

OWNER_ID = 7075336557
BANNED_DB_ID = 111
BANNED_REDIS_ID = 222
CLEAN_ID = 333


class _FakeRedisBans:
    """Mutable fake for admin_state.is_user_banned (no real Redis)."""

    def __init__(self) -> None:
        self.banned: set[int] = set()

    async def is_user_banned(self, user_id: int) -> bool:
        return user_id in self.banned


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the bot DB at a temp file so tests never touch the real DB."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bot_test.db"))
    db.init_db()
    # set_banned only updates existing rows — seed users first (matches
    # tests/test_bot_admin.py).
    now = int(__import__("time").time())
    for uid, uname in (
        (BANNED_DB_ID, "banned_db"),
        (BANNED_REDIS_ID, "banned_redis"),
        (OWNER_ID, "owner"),
        (CLEAN_ID, "clean"),
    ):
        conn = db.get_db()
        conn.execute(
            "INSERT OR REPLACE INTO users (user_id, username, created_at, last_active) VALUES (?, ?, ?, ?)",
            (uid, uname, now, now),
        )
        conn.commit()
        conn.close()
    db.set_banned(BANNED_DB_ID, True)
    # Owner must never be banned by these tests' Redis fake.
    db.set_banned(OWNER_ID, False)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedisBans:
    """Patch the Redis-backed ban store for the whole test (fails open)."""
    fake = _FakeRedisBans()
    monkeypatch.setattr(admin_state, "is_user_banned", fake.is_user_banned)
    return fake


def _user(uid: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(id=uid)


def _message_update(uid: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        effective_user=_user(uid),
        effective_message=types.SimpleNamespace(reply_text=AsyncMock()),
        callback_query=None,
        inline_query=None,
    )


def _callback_update(uid: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        effective_user=_user(uid),
        effective_message=None,
        callback_query=types.SimpleNamespace(answer=AsyncMock()),
        inline_query=None,
    )


def _inline_update(uid: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        effective_user=_user(uid),
        effective_message=None,
        callback_query=None,
        inline_query=types.SimpleNamespace(answer=AsyncMock()),
    )


def _make_gate_app() -> tuple[object, AsyncMock]:
    """Build a fake Application, install the ban gate, return (app, process_update mock)."""
    app = types.SimpleNamespace()
    app.process_update = AsyncMock()
    original = app.process_update
    bot.install_ban_gate(app)
    return app, original


def _run(coro: object) -> object:
    return asyncio.run(coro)


class TestBanGate:
    def test_sqlite_banned_user_blocked_from_message(self) -> None:
        app, original = _make_gate_app()
        update = _message_update(BANNED_DB_ID)
        _run(app.process_update(update))
        original.assert_not_awaited()
        update.effective_message.reply_text.assert_awaited_once()
        text = update.effective_message.reply_text.await_args.args[0]
        assert "banned" in text.lower()

    def test_redis_banned_user_blocked_from_message(self, fake_redis: _FakeRedisBans) -> None:
        fake_redis.banned = {BANNED_REDIS_ID}
        app, original = _make_gate_app()
        update = _message_update(BANNED_REDIS_ID)
        _run(app.process_update(update))
        original.assert_not_awaited()
        update.effective_message.reply_text.assert_awaited_once()

    def test_banned_user_blocked_from_callback(self, fake_redis: _FakeRedisBans) -> None:
        fake_redis.banned = {BANNED_REDIS_ID}
        app, original = _make_gate_app()
        update = _callback_update(BANNED_REDIS_ID)
        _run(app.process_update(update))
        original.assert_not_awaited()
        update.callback_query.answer.assert_awaited_once()

    def test_banned_user_blocked_from_inline(self, fake_redis: _FakeRedisBans) -> None:
        fake_redis.banned = {BANNED_REDIS_ID}
        app, original = _make_gate_app()
        update = _inline_update(BANNED_REDIS_ID)
        _run(app.process_update(update))
        original.assert_not_awaited()
        update.inline_query.answer.assert_awaited_once()

    def test_owner_exempt_from_ban_check(self, fake_redis: _FakeRedisBans) -> None:
        fake_redis.banned = {OWNER_ID}
        app, original = _make_gate_app()
        update = _message_update(OWNER_ID)
        _run(app.process_update(update))
        original.assert_awaited_once_with(update)
        update.effective_message.reply_text.assert_not_awaited()

    def test_clean_user_passes_through(self) -> None:
        app, original = _make_gate_app()
        update = _message_update(CLEAN_ID)
        _run(app.process_update(update))
        original.assert_awaited_once_with(update)
        update.effective_message.reply_text.assert_not_awaited()

    def test_gate_fails_open_when_redis_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        app, original = _make_gate_app()
        # Redis store dies mid-flight: admin_state.is_user_banned raises. The
        # gate must catch it, fall through to SQLite (clean user -> not banned)
        # and allow the update through.
        monkeypatch.setattr(admin_state, "is_user_banned", AsyncMock(side_effect=RuntimeError("redis down")))
        update = _message_update(CLEAN_ID)
        _run(app.process_update(update))
        original.assert_awaited_once_with(update)
