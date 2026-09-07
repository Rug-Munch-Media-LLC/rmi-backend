"""Security tests: account lockout after repeated failed logins.

Audit 2026-09-06 C-1: /auth/login had no lockout — unlimited brute force.
These tests assert the lockout counter behavior directly (Redis-backed,
5 attempts / 15 min window) and that a locked account gets 429, not 401.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


class FakeRedis:
    """Minimal async Redis stand-in for lockout counter tests."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.ttls[key] = ttl

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fr = FakeRedis()
    monkeypatch.setattr("app.core.redis.get_redis_async", lambda: fr)
    return fr


@pytest.mark.asyncio
async def test_lockout_triggers_after_max_attempts(fake_redis):
    from app.routers.auth import LOCKOUT_MAX_ATTEMPTS, _check_lockout, _record_failed_login

    email = "victim@example.com"
    for _ in range(LOCKOUT_MAX_ATTEMPTS):
        await _record_failed_login(email)
    assert await _check_lockout(email) is True


@pytest.mark.asyncio
async def test_no_lockout_below_threshold(fake_redis):
    from app.routers.auth import _check_lockout, _record_failed_login

    email = "under@example.com"
    await _record_failed_login(email)
    await _record_failed_login(email)
    assert await _check_lockout(email) is False


@pytest.mark.asyncio
async def test_lockout_window_ttl_is_15_minutes(fake_redis):
    from app.routers.auth import LOCKOUT_WINDOW_SECONDS, _record_failed_login

    await _record_failed_login("ttl@example.com")
    keys = [k for k in fake_redis.ttls if k.startswith("rmi:auth:lockout:")]
    assert keys, "lockout counter key must have a TTL set"
    assert fake_redis.ttls[keys[0]] == 900 == LOCKOUT_WINDOW_SECONDS


@pytest.mark.asyncio
async def test_successful_login_clears_counter(fake_redis):
    from app.routers.auth import LOCKOUT_MAX_ATTEMPTS, _check_lockout, _clear_failed_logins, _record_failed_login

    email = "recover@example.com"
    for _ in range(LOCKOUT_MAX_ATTEMPTS):
        await _record_failed_login(email)
    await _clear_failed_logins(email)
    assert await _check_lockout(email) is False


@pytest.mark.asyncio
async def test_lockout_key_is_pii_safe():
    """The Redis key must never contain the raw email address."""
    from app.routers.auth import _lockout_key

    key = _lockout_key("alice@example.com")
    assert "alice@example.com" not in key
    assert key.startswith("rmi:auth:lockout:")


@pytest.mark.asyncio
async def test_locked_account_gets_429_not_401(fake_redis, monkeypatch):
    """A locked-out account must receive 429 (lockout), not 401 (bad creds)."""
    from fastapi import HTTPException

    from app.routers import auth as auth_router

    email = "locked@example.com"
    for _ in range(5):
        await auth_router._record_failed_login(email)

    # sign_in must never even be attempted while locked
    monkeypatch.setattr(auth_router, "_check_lockout", AsyncMock(return_value=True))
    with pytest.raises(HTTPException) as exc_info:
        await auth_router.login(auth_router.LoginRequest(email=email, password="whatever1"))
    assert exc_info.value.status_code == 429
