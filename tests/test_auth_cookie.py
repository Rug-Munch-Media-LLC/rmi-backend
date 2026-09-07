"""Tests for the httpOnly ``rmi_token`` cookie set on auth success paths.

Frontend #240 (coordinated): the frontend relies on an httpOnly ``rmi_token``
cookie for browser sessions. These tests verify the backend sets it on login
and signup, keeps the Authorization-header (body token) path working, and
clears it on logout.

The router is exercised through a minimal FastAPI app with ``get_auth_db``
mocked, so no live Supabase/Redis is required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.routers.auth import router as auth_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth_router)
    return app


_TEST_ACCESS_TOKEN = "jwt-access-token-123"


def _session(access_token: str = _TEST_ACCESS_TOKEN) -> dict:
    """A Supabase-style session dict as returned by sign_in/sign_up."""
    return {
        "access_token": access_token,
        "refresh_token": "jwt-refresh-token-456",
        "expires_in": 3600,
        "token_type": "bearer",
        "user": {"id": "user-1", "email": "test@rugmunch.io"},
    }


@pytest.fixture
async def client():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _mock_auth_db(sign_in=None, sign_up=None):
    """Patch get_auth_db to return a fake client with async sign_in/sign_up.

    The router imports ``get_auth_db`` lazily from ``app.services.auth_db``,
    so we patch it at its definition site.
    """
    fake_db = AsyncMock()
    fake_db.db = AsyncMock()
    if sign_in is not None:
        fake_db.db.sign_in = AsyncMock(return_value=sign_in)
    if sign_up is not None:
        fake_db.db.sign_up = AsyncMock(return_value=sign_up)
    return patch("app.services.auth_db.get_auth_db", return_value=fake_db)


class TestLoginCookie:
    """Login sets the httpOnly rmi_token cookie and keeps the body token."""

    @pytest.mark.asyncio
    async def test_login_sets_http_only_cookie(self, client):
        """Login response carries a Set-Cookie for rmi_token with HttpOnly."""
        with _mock_auth_db(sign_in=_session()):
            resp = await client.post("/auth/login", json={"email": "a@b.com", "password": "password123"})
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "rmi_token=jwt-access-token-123" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/" in set_cookie

    @pytest.mark.asyncio
    async def test_login_returns_token_in_body(self, client):
        """The token stays in the body so Authorization-header clients work."""
        with _mock_auth_db(sign_in=_session()):
            resp = await client.post("/auth/login", json={"email": "a@b.com", "password": "password123"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"] == "jwt-access-token-123"
        assert body["refresh_token"] == "jwt-refresh-token-456"

    @pytest.mark.asyncio
    async def test_login_cookie_secure_in_prod(self, client):
        """In production the cookie is marked Secure."""
        with patch("app.routers.auth.settings.environment", "prod"), _mock_auth_db(sign_in=_session()):
            resp = await client.post("/auth/login", json={"email": "a@b.com", "password": "password123"})
        set_cookie = resp.headers.get("set-cookie", "")
        assert "Secure" in set_cookie

    @pytest.mark.asyncio
    async def test_login_cookie_not_secure_in_dev(self, client):
        """In dev the cookie is NOT marked Secure (local HTTP must work)."""
        with patch("app.routers.auth.settings.environment", "dev"), _mock_auth_db(sign_in=_session()):
            resp = await client.post("/auth/login", json={"email": "a@b.com", "password": "password123"})
        set_cookie = resp.headers.get("set-cookie", "")
        assert "Secure" not in set_cookie


class TestSignupCookie:
    """Signup sets the httpOnly cookie when a session token is returned."""

    @pytest.mark.asyncio
    async def test_signup_sets_cookie_when_token_present(self, client):
        """Signup with a returned session sets the rmi_token cookie."""
        with _mock_auth_db(sign_up=_session()):
            resp = await client.post(
                "/auth/signup", json={"email": "a@b.com", "password": "password123", "tier": "free"}
            )
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "rmi_token=jwt-access-token-123" in set_cookie
        assert "HttpOnly" in set_cookie

    @pytest.mark.asyncio
    async def test_signup_no_cookie_without_token(self, client):
        """Signup without a session token (e.g. email confirmation) sets no cookie."""
        with _mock_auth_db(sign_up={"user": {"id": "u1", "email": "a@b.com"}}):
            resp = await client.post(
                "/auth/signup", json={"email": "a@b.com", "password": "password123", "tier": "free"}
            )
        assert resp.status_code == 200
        assert "set-cookie" not in resp.headers


class TestLogoutCookie:
    """Logout clears the rmi_token cookie."""

    @pytest.mark.asyncio
    async def test_logout_clears_cookie(self, client):
        """Logout response clears rmi_token (max-age=0 / empty value)."""
        resp = await client.post("/auth/logout")
        assert resp.status_code == 200
        set_cookie = resp.headers.get("set-cookie", "")
        assert "rmi_token=" in set_cookie
        assert "HttpOnly" in set_cookie
        # Clearing cookie: empty value and/or max-age=0
        assert "max-age=0" in set_cookie.lower() or "rmi_token=;" in set_cookie
