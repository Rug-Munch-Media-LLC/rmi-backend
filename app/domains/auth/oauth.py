"""OAuth authentication flows (Google, GitHub, X/Twitter)."""

import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

from app.domains.auth.cookie import _set_auth_cookie, _set_csrf_cookie
from app.domains.auth.jwt import _create_jwt, _derive_user_id
from app.domains.auth.router import router
from app.domains.auth.store import _get_user_by_email

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://rugmunch.io")


# ═══════════════════════════════════════════
# Google OAuth
# ═══════════════════════════════════════════

@router.get("/google/url")
async def google_auth_url() -> dict[str, Any]:
    """Get Google OAuth URL."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/google/callback")
    if not client_id:
        return {"url": "/auth/callback?error=google_not_configured"}
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "prompt": "consent",
    }
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return {"url": url}


async def _google_token_exchange(code: str) -> dict | None:
    """Exchange authorization code for Google access token."""
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/google/callback")
    if not client_id or not client_secret:
        return None
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            return None
        return resp.json()


async def _google_get_user(access_token: str) -> dict | None:
    """Get Google user info from access token."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            return None
        return resp.json()


async def _create_or_get_user(email: str, display_name: str, provider: str = "google") -> dict:
    """Find existing user or create a new one."""
    user = _get_user_by_email(email)
    if user:
        return user
    user_id = _derive_user_id(email)
    user = {
        "id": user_id,
        "email": email,
        "display_name": display_name,
        "tier": "FREE",
        "role": "USER",
        "created_at": datetime.now(UTC).isoformat(),
        "xp": 0,
        "level": 1,
        "badges": [],
        "scans_remaining": 5,
        "scans_used": 0,
    }
    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    r.hset("rmi:users", user_id, json.dumps(user))
    r.hset("rmi:users:email", email.lower(), user_id)
    return user


@router.get("/google/callback")
async def google_callback(request: Request) -> Any:
    """Handle Google OAuth redirect."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error={error}")
    if not code:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=missing_code")

    tokens = await _google_token_exchange(code)
    if not tokens:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=token_exchange_failed")

    google_user = await _google_get_user(tokens.get("access_token"))
    if not google_user:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=userinfo_failed")

    email = google_user.get("email")
    user = await _create_or_get_user(email, google_user.get("name", email))
    jwt_token = _create_jwt(user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER"))
    redirect = RedirectResponse(f"{FRONTEND_URL}/auth/callback?provider=google")
    _set_auth_cookie(redirect, jwt_token, request)
    return redirect


@router.post("/google/callback")
async def google_callback_post(request: Request) -> JSONResponse:
    """Legacy POST endpoint for manual code exchange."""
    body = await request.json()
    code = body.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    tokens = await _google_token_exchange(code)
    if not tokens:
        raise HTTPException(status_code=400, detail="Failed to exchange code")

    google_user = await _google_get_user(tokens.get("access_token"))
    if not google_user:
        raise HTTPException(status_code=400, detail="Failed to get user info")

    email = google_user.get("email")
    user = await _create_or_get_user(email, google_user.get("name", email))
    jwt_token = _create_jwt(user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER"))

    body = {
        "access_token": jwt_token,
        "refresh_token": jwt_token,
        "user": {
            "id": user["id"],
            "email": user["email"],
            "display_name": user.get("display_name", email),
            "wallet_address": None,
            "wallet_chain": None,
            "tier": user.get("tier", "FREE"),
            "role": user.get("role", "USER"),
            "created_at": user.get("created_at"),
        },
    }
    response = JSONResponse(content=body, status_code=200)
    _set_auth_cookie(response, jwt_token, request)
    _set_csrf_cookie(response, request)
    return response


# ═══════════════════════════════════════════
# GitHub OAuth
# ═══════════════════════════════════════════

@router.get("/github/url")
async def github_auth_url() -> dict[str, Any]:
    """Get GitHub OAuth URL."""
    client_id = os.getenv("GITHUB_CLIENT_ID")
    redirect_uri = os.getenv("GITHUB_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/github/callback")
    if not client_id:
        return {"url": "/auth/callback?error=github_not_configured"}
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "scope": "user:email"}
    url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    return {"url": url}


@router.get("/github/callback")
async def github_callback(request: Request) -> Any:
    """Handle GitHub OAuth redirect."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error={error}")
    if not code:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=missing_code")

    client_id = os.getenv("GITHUB_CLIENT_ID")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET")
    redirect_uri = os.getenv("GITHUB_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/github/callback")
    if not client_id or not client_secret:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=not_configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            data={"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": redirect_uri},
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=token_exchange_failed")
        tokens = resp.json()
        access_token = tokens.get("access_token")

        resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"token {access_token}", "Accept": "application/json"},
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=userinfo_failed")
        github_user = resp.json()
        email = github_user.get("email")

        if not email:
            resp = await client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"token {access_token}", "Accept": "application/json"},
            )
            if resp.status_code == 200:
                emails = resp.json()
                primary = next((e for e in emails if e.get("primary")), None)
                email = primary.get("email") if primary else (emails[0].get("email") if emails else None)

        if not email:
            email = f"{github_user.get('login', 'github_user')}@github.rmi"

        user = await _create_or_get_user(email, github_user.get("name", github_user.get("login", email)))
        jwt_token = _create_jwt(user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER"))
        redirect = RedirectResponse(f"{FRONTEND_URL}/auth/callback?provider=github")
        _set_auth_cookie(redirect, jwt_token, request)
        return redirect


# ═══════════════════════════════════════════
# X/Twitter OAuth
# ═══════════════════════════════════════════

@router.get("/x/url")
async def x_auth_url() -> dict[str, Any]:
    """Get X/Twitter OAuth URL."""
    client_id = os.getenv("X_CLIENT_ID")
    redirect_uri = os.getenv("X_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/x/callback")
    if not client_id:
        return {"url": "/auth/callback?error=x_not_configured"}
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": "tweet.read users.read"}
    url = f"https://twitter.com/i/oauth2/authorize?{urlencode(params)}"
    return {"url": url}


@router.get("/x/callback")
async def x_callback(request: Request) -> Any:
    """Handle X/Twitter OAuth redirect."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error={error}")
    if not code:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=missing_code")

    client_id = os.getenv("X_CLIENT_ID")
    client_secret = os.getenv("X_CLIENT_SECRET")
    redirect_uri = os.getenv("X_REDIRECT_URI", "https://rugmunch.io/api/v1/auth/x/callback")
    if not client_id or not client_secret:
        return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=not_configured")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.twitter.com/2/oauth2/token",
            data={
                "code": code, "grant_type": "authorization_code",
                "client_id": client_id, "redirect_uri": redirect_uri,
                "code_verifier": "challenge",
            },
            auth=(client_id, client_secret),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=token_exchange_failed")
        tokens = resp.json()
        access_token = tokens.get("access_token")

        resp = await client.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            return RedirectResponse(f"{FRONTEND_URL}/auth/callback?error=userinfo_failed")
        x_user = resp.json().get("data", {})
        username = x_user.get("username", f"x_user_{x_user.get('id', 'unknown')}")
        email = f"{username}@x.rmi"

        user = await _create_or_get_user(email, x_user.get("name", username))
        jwt_token = _create_jwt(user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER"))
        redirect = RedirectResponse(f"{FRONTEND_URL}/auth/callback?provider=x")
        _set_auth_cookie(redirect, jwt_token, request)
        return redirect
