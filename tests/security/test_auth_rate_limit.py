"""Security tests: rate limiting on /auth/login and /auth/signup.

Audit 2026-09-06 C-1: the two most security-critical endpoints had no rate
limiting. These tests assert the rate-limit dependency is actually attached
to both routes with the intended (rate, burst) budgets — a structural check
that survives refactors and cannot pass vacuously.
"""

from __future__ import annotations

import pytest

from app.core.rate_limiter import RateLimitDependency
from app.routers.auth import router


def _route(path: str):
    return next(r for r in router.routes if getattr(r, "path", "") == path)


def _rate_limit_deps(route) -> list[RateLimitDependency]:
    return [d.call for d in route.dependant.dependencies if isinstance(d.call, RateLimitDependency)]


def test_login_has_rate_limit_dependency():
    deps = _rate_limit_deps(_route("/auth/login"))
    assert deps, "login route must declare a rate-limit dependency"
    dep = deps[0]
    assert dep.limit_key == "login"
    assert dep._rate == pytest.approx(0.1)  # 6 req/min
    assert dep._burst == 5


def test_signup_has_rate_limit_dependency():
    deps = _rate_limit_deps(_route("/auth/signup"))
    assert deps, "signup route must declare a rate-limit dependency"
    dep = deps[0]
    assert dep.limit_key == "signup"
    assert dep._rate == pytest.approx(0.05)  # 3 req/min
    assert dep._burst == 3


def test_rate_limit_dependency_raises_429_when_exhausted():
    """The dependency itself must reject with 429, not silently pass."""
    import asyncio

    from fastapi import HTTPException

    dep = RateLimitDependency(limit_key="login", rate=0.001, burst=1)
    request = type(
        "R",
        (),
        {"url": type("U", (), {"path": "/auth/login"})(), "client": None, "headers": {}, "state": type("S", (), {})()},
    )()

    async def run():
        await dep(request)  # first call consumes the single burst token
        with pytest.raises(HTTPException) as exc_info:
            await dep(request)
        assert exc_info.value.status_code == 429

    asyncio.run(run())
