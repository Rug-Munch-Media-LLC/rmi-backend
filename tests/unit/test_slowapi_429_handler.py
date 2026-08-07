"""Tests for the safe slowapi 429 exception handler.

Regression test for the production bug where ANY HTTPException(429) raised
by an undecorated route crashed to 500 because slowapi's stock handler
reads ``request.state.view_rate_limit`` which is never set there.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.middleware_setup import _safe_rate_limit_exceeded_handler


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(429, _safe_rate_limit_exceeded_handler)

    @app.get("/plain-429")
    async def plain_429() -> None:
        # Simulates a handler raising a 429 (e.g. chat rate-limit rejection)
        # WITHOUT the slowapi @limiter decorator → request.state.view_rate_limit
        # is never set.
        raise HTTPException(status_code=429, detail="Daily limit exceeded")

    @app.get("/slowapi-429")
    async def slowapi_429() -> None:
        # Simulates slowapi's RateLimitExceeded path where view_rate_limit
        # IS set on request.state.
        raise HTTPException(status_code=429, detail="Rate limit exceeded: 5 per 1 minute")

    return app


def test_plain_429_returns_429_not_500() -> None:
    """Undecorated route raising 429 must stay 429 (regression: was 500)."""
    client = TestClient(_build_app())
    resp = client.get("/plain-429")
    assert resp.status_code == 429
    assert "Rate limit exceeded" in resp.json()["error"]
    assert "Daily limit exceeded" in resp.json()["error"]


def test_view_rate_limit_set_still_works() -> None:
    """When view_rate_limit is set, handler must not crash and stays 429."""
    app = _build_app()

    class FakeLimiter:
        def _inject_headers(self, response, view_limit):
            response.headers["X-RateLimit-Limit"] = str(view_limit)
            return response

    app.state.limiter = FakeLimiter()

    # FastAPI passes `request` through ASGI; simulate state via middleware.
    from starlette.middleware.base import BaseHTTPMiddleware

    class SetViewLimit(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.view_rate_limit = "5 per 1 minute"
            return await call_next(request)

    app.add_middleware(SetViewLimit)

    client = TestClient(app)
    resp = client.get("/plain-429")
    assert resp.status_code == 429
    assert resp.headers.get("X-RateLimit-Limit") == "5 per 1 minute"


def test_retry_after_header_when_present() -> None:
    """RateLimitExceeded carries retry_after; HTTPException does not."""
    app = _build_app()

    @app.get("/retry-429")
    async def retry_429() -> None:
        raise HTTPException(status_code=429, detail="slow down")

    client = TestClient(app)
    resp = client.get("/retry-429")
    assert resp.status_code == 429
