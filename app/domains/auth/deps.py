"""FastAPI dependency functions for auth."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from app.domains.auth.jwt import _verify_jwt
from app.domains.auth.store import _get_user

def _extract_token(request: Request) -> str | None:
    """Extract JWT from cookie first, then Authorization header fallback."""
    cookie_token = request.cookies.get("rmi_token")
    if cookie_token:
        return cookie_token
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None

async def get_current_user(request: Request) -> dict[str, Any] | None:
    """Verify JWT from cookie or Authorization header."""
    token = _extract_token(request)
    if not token:
        return None
    payload = _verify_jwt(token)
    if not payload:
        return None
    user = _get_user(payload["id"])
    if not user:
        return None
    return user

async def require_auth(request: Request) -> dict[str, Any]:
    """Require valid JWT. Raises 401 if missing/invalid."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user

async def require_public_profile(request: Request) -> dict[str, Any]:
    """Require authenticated user with a public profile."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    privacy_settings = user.get("privacy_settings", {})
    visibility = privacy_settings.get("profile_visibility", "public")
    if visibility != "public":
        raise HTTPException(
            status_code=403,
            detail="A public profile is required. Please update your privacy settings.",
        )
    return user
