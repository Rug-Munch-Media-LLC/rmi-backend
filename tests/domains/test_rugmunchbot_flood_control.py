"""Unit tests for rugmunchbot flood control (per-second / per-minute, SQLite fallback).

The flood controller contract under test:
  check_flood(user_id) -> (allowed, retry_after_seconds, receipt)
  refund_flood(user_id, receipt)  # tier-3 redirects are not counted

Redis is force-disabled (redis_enabled=False) so every test exercises the
SQLite timestamp-based fallback deterministically. The bot DB is redirected
to a temp file per test so the real bot_users.db is never touched.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domains.telegram.rugmunchbot import db, flood_control as fc


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the bot DB at a temp file so tests never touch the real DB."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "bot_test.db"))


def _run(coro: object) -> object:
    return asyncio.run(coro)


class TestFloodControl:
    def test_per_second_limit(self) -> None:
        ctrl = fc.FloodController(per_second=3, per_minute=1000, redis_enabled=False)
        # 3 rapid messages allowed within the same second
        for _ in range(3):
            allowed, _, _ = _run(ctrl.check_and_count(123))
            assert allowed is True
        # 4th within the same second -> blocked, with a positive retry window
        allowed, retry_after, _ = _run(ctrl.check_and_count(123))
        assert allowed is False
        assert retry_after >= 1

    def test_per_minute_limit(self) -> None:
        ctrl = fc.FloodController(per_second=1000, per_minute=20, redis_enabled=False)
        for _ in range(20):
            allowed, _, _ = _run(ctrl.check_and_count(456))
            assert allowed is True
        allowed, retry_after, _ = _run(ctrl.check_and_count(456))
        assert allowed is False
        assert retry_after >= 1

    def test_refund_undoes_count_for_tier3(self) -> None:
        ctrl = fc.FloodController(per_second=1000, per_minute=3, redis_enabled=False)
        receipts = []
        for _ in range(3):
            allowed, _, receipt = _run(ctrl.check_and_count(789))
            assert allowed is True
            receipts.append(receipt)
        # 4th blocked
        assert _run(ctrl.check_and_count(789))[0] is False
        # Refund one count (simulates a tier-3 redirect) -> 4th allowed again
        _run(ctrl.refund(789, receipts[0]))
        allowed, _, _ = _run(ctrl.check_and_count(789))
        assert allowed is True

    def test_owner_bypass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.domains.telegram.rugmunchbot import states

        monkeypatch.setattr(states, "OWNER_IDS", {999000111})
        ctrl = fc.FloodController(per_second=1, per_minute=1, redis_enabled=False)
        for _ in range(5):
            allowed, _, _ = _run(ctrl.check_and_count(999000111))
            assert allowed is True

    def test_per_user_isolation(self) -> None:
        ctrl = fc.FloodController(per_second=1000, per_minute=2, redis_enabled=False)
        assert _run(ctrl.check_and_count(1))[0] is True
        assert _run(ctrl.check_and_count(1))[0] is True
        # A different user has an independent budget
        assert _run(ctrl.check_and_count(2))[0] is True
        assert _run(ctrl.check_and_count(2))[0] is True
        # User 1 is now over their per-minute budget
        assert _run(ctrl.check_and_count(1))[0] is False
