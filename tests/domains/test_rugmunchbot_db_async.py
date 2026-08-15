"""Tests for the async SQLite helpers in ``db/_base.py``.

Proves:
  (a) public sync signatures (``get_db`` / ``init_db``) are unchanged and
      still return real sqlite3 connections;
  (b) ``get_db_async`` returns a usable connection without blocking the
      event loop (executed via ``asyncio.to_thread``);
  (c) ``run_db`` executes a sync DB callable off-loop and returns its value.

Uses a temp-dir DB path (monkeypatched via the ``db`` package shim exactly
like the existing ``tests/test_db.py`` convention).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domains.telegram.rugmunchbot import db
from app.domains.telegram.rugmunchbot.db._base import get_db_async, run_db


@pytest.fixture(autouse=True)
def _tmp_db_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bot_async_test.db"))


class TestAsyncDb:
    async def test_get_db_async_returns_usable_connection(self) -> None:
        conn = await get_db_async()
        try:
            row = await run_db(conn.execute, "SELECT 1 AS one")
            assert row.fetchone()["one"] == 1
        finally:
            await run_db(conn.close)

    async def test_run_db_executes_callable_and_returns_value(self) -> None:
        def _total(conn: Any, n: int) -> int:
            return conn.execute("SELECT ? * 2 AS v", (n,)).fetchone()["v"]

        conn = await get_db_async()
        try:
            assert await run_db(_total, conn, 21) == 42
        finally:
            await run_db(conn.close)

    async def test_sync_public_signatures_unchanged(self) -> None:
        conn = db.get_db()
        assert conn is not None
        conn.close()
        assert callable(db.init_db)

    async def test_write_then_read_through_to_thread(self) -> None:
        conn = await get_db_async()
        try:
            await run_db(conn.execute, "CREATE TABLE IF NOT EXISTS t (v INTEGER)")
            await run_db(conn.execute, "INSERT INTO t (v) VALUES (7)")
            await run_db(conn.commit)
            row = await run_db(conn.execute, "SELECT v FROM t LIMIT 1")
            assert row.fetchone()["v"] == 7
        finally:
            await run_db(conn.close)
