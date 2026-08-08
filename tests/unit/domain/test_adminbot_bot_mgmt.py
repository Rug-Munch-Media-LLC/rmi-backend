"""Tests for adminbot RugMunchBot management + ops (Task: admin panel v6).

Extends ``tests/domains/test_adminbot_health.py`` with the *behavioural*
paths that file's fail-open tests skip:

  - ``bot_stats`` with a seeded SQLite DB (real counts, not just shape)
  - ``bot_toggle`` / ``bot_toggle_chat`` with mocked Redis state + SQLite
  - ``bot_broadcast`` success (queued) path
  - ``bot_broadcasts`` rendering of records
  - ``bot_users`` with seeded rows
  - ``scanner_today`` / ``scanner_stats`` with real query results
  - ``admin_ops.backup_report`` with a present sentinel (green/yellow)
  - ``admin_ops.scan_stats`` with a mocked backend HTTP response

The adminbot ``config`` module fatally exits without token env vars, so they
are seeded before any adminbot import.
"""

from __future__ import annotations

import os
import sqlite3
import time

os.environ.setdefault("ADMIN_BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_CHAT_ID", "7075336557")

from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.domains.telegram.adminbot import admin_bot, admin_ops

TODAY = time.strftime("%Y-%m-%d")


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def bot_db(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> str:
    """Create a real RugMunchBot SQLite DB with seeded rows.

    Returns the DB path. ``BOT_DB_PATH`` is set so ``admin_bot._resolve_db_path``
    (which reads the env override first) finds it.
    """
    db_path = str(tmp_path / "bot_users.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT, first_name TEXT, tier TEXT DEFAULT 'free',
            total_scans INTEGER DEFAULT 0, total_ai_msgs INTEGER DEFAULT 0,
            created_at INTEGER, last_active INTEGER, is_banned INTEGER DEFAULT 0
        );
        CREATE TABLE daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE, messages INTEGER DEFAULT 0
        );
        CREATE TABLE scan_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, token_address TEXT, chain TEXT,
            scan_type TEXT, risk_score REAL, created_at INTEGER
        );
        CREATE TABLE bot_state (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    now = int(time.time())
    conn.executemany(
        "INSERT INTO users (user_id, username, first_name, tier, total_scans, created_at, last_active, is_banned) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "alice", "Alice", "free", 5, now - 86400, now - 60, 0),
            (2, "bob", "Bob", "pro", 12, now - 86400, now - 3600, 0),
            (3, "mallory", "Mallory", "free", 1, now - 86400, now - 7200, 1),
        ],
    )
    conn.execute("INSERT INTO daily_stats (date, messages) VALUES (?, ?)", (TODAY, 42))
    start_of_day = int(time.mktime(time.strptime(TODAY, "%Y-%m-%d")))
    conn.executemany(
        "INSERT INTO scan_history (user_id, token_address, chain, scan_type, risk_score, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (1, "0xAAA", "ethereum", "quick", 30.0, now - 100),
            (2, "0xBBB", "ethereum", "deep", 70.0, now - 200),
            (2, "0xCCC", "solana", "quick", 45.0, now - 86400 * 3),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("BOT_DB_PATH", db_path)
    return db_path


@pytest.fixture
def fake_admin_state() -> Mock:
    """Mock module for ``app.domains.telegram.rugmunchbot.admin_state``."""
    return Mock(
        get_bot_state=AsyncMock(return_value={"enabled": True, "chat_enabled": True}),
        set_bot_state=AsyncMock(return_value={"enabled": False, "chat_enabled": True}),
        get_daily_analytics=AsyncMock(return_value=[{"messages": 99}]),
        enqueue_broadcast=AsyncMock(return_value={"id": "bcast1", "status": "queued", "error": ""}),
        list_broadcasts=AsyncMock(
            return_value=[
                {"id": "b1", "status": "completed", "sent": 10, "total": 10, "failed": 0, "message": "gm"},
                {"id": "b2", "status": "failed", "sent": 0, "total": 5, "failed": 5, "message": "rug alert"},
            ]
        ),
    )


@pytest.fixture
def fake_bot_db() -> Mock:
    """Mock module for ``app.domains.telegram.rugmunchbot.db``."""
    return Mock(set_bot_state=Mock(return_value=None))


# ═══════════════════════════════════════════════
# Control-panel dashboard — bot_stats
# ═══════════════════════════════════════════════


class TestBotStats:
    @pytest.mark.asyncio
    async def test_bot_stats_with_seeded_db(self, bot_db: str) -> None:
        """bot_stats reads real user/scan counts from the bot SQLite DB."""
        fake_state = Mock(
            get_bot_state=AsyncMock(return_value={"enabled": True, "chat_enabled": True}),
            get_daily_analytics=AsyncMock(return_value=[]),
        )
        with patch("app.domains.telegram.rugmunchbot.admin_state", fake_state):
            result = await admin_bot.bot_stats()
        assert result["ok"] is True
        assert result["users"] == 3
        assert result["banned"] == 1
        assert result["active_today"] == 3  # all three active within today
        assert result["scans_today"] == 2  # two scans within today
        assert result["messages_today"] == 42  # SQLite daily_stats value

    @pytest.mark.asyncio
    async def test_bot_stats_prefers_redis_analytics_for_messages(self, bot_db: str, fake_admin_state: Mock) -> None:
        """When Redis analytics are available they win over the SQLite counter."""
        with patch("app.domains.telegram.rugmunchbot.admin_state", fake_admin_state):
            result = await admin_bot.bot_stats()
        # 99 from Redis beats the SQLite 42.
        assert result["messages_today"] == 99

    @pytest.mark.asyncio
    async def test_bot_stats_fails_open_when_db_missing(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A missing/unreadable DB yields zero counts, never raises."""
        monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "does-not-exist.db"))
        fake_state = Mock(
            get_bot_state=AsyncMock(return_value={"enabled": True, "chat_enabled": True}),
            get_daily_analytics=AsyncMock(return_value=[]),
        )
        with patch("app.domains.telegram.rugmunchbot.admin_state", fake_state):
            result = await admin_bot.bot_stats()
        assert result["ok"] is True
        assert result["users"] == 0
        assert result["banned"] == 0


# ═══════════════════════════════════════════════
# Bot management commands
# ═══════════════════════════════════════════════


class TestBotToggle:
    @pytest.mark.asyncio
    async def test_bot_toggle_disables_bot(self, fake_admin_state: Mock, fake_bot_db: Mock) -> None:
        """bot_toggle(False) updates Redis state and mirrors into SQLite."""
        with (
            patch("app.domains.telegram.rugmunchbot.admin_state", fake_admin_state),
            patch("app.domains.telegram.rugmunchbot.db", fake_bot_db),
        ):
            result = await admin_bot.bot_toggle(False)

        assert result["ok"] is True
        fake_admin_state.set_bot_state.assert_awaited_once_with(enabled=False)
        fake_bot_db.set_bot_state.assert_called_once_with("bot_enabled", "off")
        assert "disabled" in result["output"]

    @pytest.mark.asyncio
    async def test_bot_toggle_chat(self, fake_admin_state: Mock, fake_bot_db: Mock) -> None:
        """bot_toggle_chat(True) flips the chat_enabled flag."""
        fake_admin_state.set_bot_state = AsyncMock(return_value={"enabled": True, "chat_enabled": True})
        with (
            patch("app.domains.telegram.rugmunchbot.admin_state", fake_admin_state),
            patch("app.domains.telegram.rugmunchbot.db", fake_bot_db),
        ):
            result = await admin_bot.bot_toggle_chat(True)

        assert result["ok"] is True
        fake_admin_state.set_bot_state.assert_awaited_once_with(chat_enabled=True)
        fake_bot_db.set_bot_state.assert_called_once_with("chat_enabled", "on")

    @pytest.mark.asyncio
    async def test_bot_toggle_error_returns_err(self, fake_bot_db: Mock) -> None:
        """Failures are reported as err, never raised."""
        with (
            patch(
                "app.domains.telegram.rugmunchbot.admin_state.set_bot_state",
                new=AsyncMock(side_effect=RuntimeError("redis down")),
            ),
            patch("app.domains.telegram.rugmunchbot.db", fake_bot_db),
        ):
            result = await admin_bot.bot_toggle(True)

        assert result["ok"] is False
        assert "Failed to toggle" in result["error"]


class TestBotBroadcast:
    @pytest.mark.asyncio
    async def test_broadcast_queued_success(self, fake_admin_state: Mock) -> None:
        """A non-empty message is queued and confirmed."""
        with patch("app.domains.telegram.rugmunchbot.admin_state", fake_admin_state):
            result = await admin_bot.bot_broadcast("hello everyone")

        assert result["ok"] is True
        assert "Broadcast queued" in result["output"]
        fake_admin_state.enqueue_broadcast.assert_awaited_once_with("hello everyone", sender="adminbot")

    @pytest.mark.asyncio
    async def test_broadcast_reports_not_queued(self, fake_admin_state: Mock) -> None:
        """A backend refusal surfaces as an error card."""
        fake_admin_state.enqueue_broadcast = AsyncMock(
            return_value={"id": "b2", "status": "error", "error": "redis_unavailable"}
        )
        with patch("app.domains.telegram.rugmunchbot.admin_state", fake_admin_state):
            result = await admin_bot.bot_broadcast("hello")

        assert result["ok"] is False
        assert "redis_unavailable" in result["error"]

    @pytest.mark.asyncio
    async def test_broadcasts_history_rendered(self, fake_admin_state: Mock) -> None:
        """Broadcast history renders status + delivery counts."""
        with patch("app.domains.telegram.rugmunchbot.admin_state", fake_admin_state):
            result = await admin_bot.bot_broadcasts(limit=10)

        assert result["ok"] is True
        assert "Broadcast History" in result["output"]
        assert "b1" in result["output"]
        assert "completed" in result["output"]
        assert "10/10/0" in result["output"]  # sent/total/failed


class TestBotUsers:
    def test_bot_users_lists_seeded_users(self, bot_db: str) -> None:
        """Users are listed most-recently-active first with tier and ban."""
        result = admin_bot.bot_users(limit=10)
        assert result["ok"] is True
        output = result["output"]
        assert "Recent RugMunchBot Users" in output
        assert "alice" in output
        assert "bob" in output
        # Mallory is banned → flagged with 🚫
        assert "🚫" in output


# ═══════════════════════════════════════════════
# Scanner stats (bot-level)
# ═══════════════════════════════════════════════


class TestScannerStatsBot:
    def test_scanner_today_counts(self, bot_db: str) -> None:
        """scanner_today returns the number of scans within today."""
        result = admin_bot.scanner_today()
        assert result["ok"] is True
        assert result["output"].strip() == "2"

    def test_scanner_stats_full(self, bot_db: str) -> None:
        """scanner_stats aggregates today/week/total + chain breakdown."""
        result = admin_bot.scanner_stats()
        assert result["ok"] is True
        output = result["output"]
        assert "Scans today: `2`" in output
        assert "Total scans: `3`" in output
        assert "Unique tokens: `3`" in output
        assert "By chain:" in output
        assert "ethereum" in output
        assert "solana" in output

    def test_scanner_stats_fails_open(self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing DB yields an err result, never raises."""
        monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "nope.db"))
        result = admin_bot.scanner_stats()
        assert result["ok"] is False


# ═══════════════════════════════════════════════
# admin_ops — backup report + scan stats
# ═══════════════════════════════════════════════


class TestBackupReport:
    def test_backup_report_green_with_fresh_sentinel(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fresh sentinel renders a green line with hours-ago."""
        sentinel = tmp_path / "last-successful-backup"
        sentinel.write_text(str(int(time.time()) - 2 * 3600))  # 2h ago
        monkeypatch.setattr(admin_ops, "_BACKUP_SENTINEL", str(sentinel))
        monkeypatch.setattr(admin_ops, "_GIT_BACKUP_SENTINEL", str(tmp_path / "no-git-sentinel"))
        monkeypatch.setattr(admin_ops.os.path, "isdir", lambda p: False)

        result = admin_ops.backup_report()
        assert result["ok"] is True
        output = result["output"]
        assert "🟢" in output  # green emoji for <36h
        assert "2.0h ago" in output

    def test_backup_report_yellow_for_stale_sentinel(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 2-day-old sentinel renders yellow (< 7 days)."""
        sentinel = tmp_path / "last-successful-backup"
        sentinel.write_text(str(int(time.time()) - 2 * 86400))
        monkeypatch.setattr(admin_ops, "_BACKUP_SENTINEL", str(sentinel))
        monkeypatch.setattr(admin_ops, "_GIT_BACKUP_SENTINEL", str(tmp_path / "no-git-sentinel"))
        monkeypatch.setattr(admin_ops.os.path, "isdir", lambda p: False)

        result = admin_ops.backup_report()
        assert "🟡" in result["output"]

    def test_backup_report_lists_artifacts(
        self, tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recent /backups artifacts are listed newest-first."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        old = backup_dir / "old.db"
        old.write_bytes(b"x")
        os.utime(old, (time.time() - 5000, time.time() - 5000))
        new = backup_dir / "new.db"
        new.write_bytes(b"x")
        os.utime(new, (time.time() - 100, time.time() - 100))

        monkeypatch.setattr(admin_ops, "_BACKUP_SENTINEL", str(tmp_path / "no-sentinel"))
        monkeypatch.setattr(admin_ops, "_GIT_BACKUP_SENTINEL", str(tmp_path / "no-git-sentinel"))
        monkeypatch.setattr(admin_ops.os.path, "isdir", lambda p: True)
        monkeypatch.setattr(admin_ops.os, "listdir", lambda p: ["old.db", "new.db"])
        monkeypatch.setattr(admin_ops.os.path, "isfile", lambda p: True)
        monkeypatch.setattr(
            admin_ops.os.path, "getmtime", lambda p: time.time() - (100 if p.endswith("new.db") else 5000)
        )

        result = admin_ops.backup_report()
        output = result["output"]
        assert "Recent backup artifacts" in output
        assert "new.db" in output
        assert "old.db" in output


class TestScanStatsOps:
    def test_scan_stats_parses_backend_response(self) -> None:
        """scan_stats forwards metrics + sufficiency JSON from the backend."""

        class _FakeResponse:
            def __init__(self, status: int, payload: dict) -> None:
                self.status_code = status
                self._payload = payload

            def json(self) -> dict:
                return self._payload

        fake_metrics = _FakeResponse(200, {"metrics": {"own_data_pct": 68, "total_checks": 120}})
        fake_suff = _FakeResponse(200, {"sufficiency": {"own_data_pct": 71, "total_questions": 14}})

        with patch("httpx.get") as mock_get:
            mock_get.side_effect = [fake_metrics, fake_suff]
            result = admin_ops.scan_stats()

        assert result["ok"] is True
        data = __import__("json").loads(result["output"])
        assert data["metrics"]["metrics"]["own_data_pct"] == 68
        assert data["sufficiency"]["sufficiency"]["own_data_pct"] == 71

    def test_scan_stats_error_on_backend_failure(self) -> None:
        """Backend connection failures degrade to an err result."""
        with patch("httpx.get", side_effect=ConnectionError("backend down")):
            result = admin_ops.scan_stats()
        assert result["ok"] is False
        assert "backend down" in result["error"]
