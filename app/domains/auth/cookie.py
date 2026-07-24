"""HttpOnly cookie helpers for JWT and CSRF tokens."""

from __future__ import annotations

import secrets

from fastapi import Request, Response

from app.domains.auth.jwt import JWT_EXPIRY_DAYS

def _is_localhost(request: Request) -> bool:
    """Check if the request is from localhost."""
    host = request.headers.get("host", "")
    return host.startswith("localhost") or host.startswith("127.0.0.1") or host.startswith("[::1]")

def _set_auth_cookie(response: Response, token: str, request: Request) -> None:
    """Set the JWT as an HttpOnly cookie."""
    max_age = JWT_EXPIRY_DAYS * 86400
    secure = not _is_localhost(request)
    response.set_cookie(
        key="rmi_token",
        value=token,
        max_age=max_age,
        expires=max_age,
        path="/",
        secure=secure,
        httponly=True,
        samesite="strict",
    )

def _set_csrf_cookie(response: Response, request: Request) -> str:
    """Generate and set a CSRF token as a non-HttpOnly cookie."""
    csrf_token = secrets.token_hex(32)
    secure = not _is_localhost(request)
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        max_age=JWT_EXPIRY_DAYS * 86400,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )
    return csrf_token

def _clear_auth_cookie(response: Response, request: Request) -> None:
    """Clear the auth cookie (logout)."""
    secure = not _is_localhost(request)
    response.set_cookie(key="rmi_token", value="", max_age=0, expires=0, path="/", secure=secure, httponly=True, samesite="strict")
    response.set_cookie(key="csrf_token", value="", max_age=0, expires=0, path="/", secure=secure, httponly=False, samesite="lax")
