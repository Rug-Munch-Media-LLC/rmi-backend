"""Regression tests for the x402 rate-limiter Redis accessor.

Incident: HTTP 5xx storms on /api/v1/x402-tools/*.

Root cause: `check_rate_limit` imported `get_redis` from
`app.routers.x402_enforcement`, a back-compat shim that stopped re-exporting
that symbol. The ImportError was raised inside a `try` that only caught
(ConnectionError, TimeoutError, OSError), so it escaped as an unhandled
exception -> HTTP 500 on every paid-tool request.

The tests below fail against the old code (which raised ImportError out of
the guard) and pass against the fixed code.
"""

from __future__ import annotations

import sys
import types
from typing import TYPE_CHECKING, Any

from fastapi import Request

from app.domains.billing.x402.tools.integration.utils import check_rate_limit

if TYPE_CHECKING:
    import pytest


def _make_request(ip: str = "203.0.113.5") -> Request:
    """Minimal ASGI request carrying a client IP."""
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/x402-tools/x",
        "headers": [],
        "client": (ip, 12345),
        "query_string": b"",
    }
    return Request(scope)


def test_check_rate_limit_does_not_import_get_redis_from_dead_shim() -> None:
    """The dead `app.routers.x402_enforcement.get_redis` import must be gone.

    Fails on old code: the symbol is still present in the source.
    Passes on new code: the accessor now lives in app.core.redis.
    """
    import inspect

    from app.domains.billing.x402.tools.integration import utils

    src = inspect.getsource(utils.check_rate_limit)
    assert "from app.routers.x402_enforcement import get_redis" not in src
    assert "from app.core.redis import get_redis" in src


def test_check_rate_limit_never_raises_when_accessor_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing/broken redis accessor must fail OPEN, not 500.

    We simulate the exact failure of the incident by making the
    `app.core.redis` accessor raise ImportError (a broken dependency graph).
    The guard must swallow it and allow the request.
    """
    fake_module = types.ModuleType("app.core.redis")

    def _boom() -> Any:
        raise ImportError("cannot import name 'get_redis'")

    fake_module.get_redis = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.core.redis", fake_module)

    # Must not raise; must fail open (True == allow request).
    assert awaitable_true(check_rate_limit(_make_request())) is True


def test_check_rate_limit_fails_open_when_redis_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If redis accessor yields None, allow the request (fail open)."""
    fake_module = types.ModuleType("app.core.redis")
    fake_module.get_redis = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.core.redis", fake_module)

    assert awaitable_true(check_rate_limit(_make_request())) is True


def test_check_rate_limit_enforces_60_per_min(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When redis is available, the 60/min limit is actually enforced."""
    calls: dict[str, int] = {}

    class _FakeRedis:
        def incr(self, key: str) -> int:
            calls[key] = calls.get(key, 0) + 1
            return calls[key]

        def expire(self, key: str, ttl: int) -> None:
            return None

    fake_module = types.ModuleType("app.core.redis")
    fake_module.get_redis = lambda: _FakeRedis()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "app.core.redis", fake_module)

    # 60 requests/min allowed; the 61st is blocked.
    for _ in range(60):
        assert awaitable_true(check_rate_limit(_make_request())) is True
    assert awaitable_true(check_rate_limit(_make_request())) is False


def awaitable_true(coro: Any) -> bool:
    """Run an async predicate to completion and return its bool value.

    Keeps the tests synchronous so they need no event-loop plumbing while
    still exercising the real async `check_rate_limit`.
    """
    import asyncio

    return asyncio.run(coro)
