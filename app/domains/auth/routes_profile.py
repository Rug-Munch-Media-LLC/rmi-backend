"""User profile and auth cookie/logout endpoints."""

from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger

logger = get_logger(__name__)

from app.domains.auth.cookie import (  # noqa: E402 — imported after logger init; kept below module header for readability
    _clear_auth_cookie,
)
from app.domains.auth.deps import (  # noqa: E402 — imported after logger init; kept below module header for readability
    _extract_token,
)
from app.domains.auth.jwt import (  # noqa: E402 — imported after logger init; kept below module header for readability
    _verify_jwt,
)
from app.domains.auth.router import (  # noqa: E402 — imported after logger init; kept below module header for readability
    router,
)
from app.domains.auth.schemas import (  # noqa: E402 — imported after logger init; kept below module header for readability
    UserResponse,
)
from app.domains.auth.store import _get_user  # noqa: E402 — module docstring precedes import block


@router.get("/user/me", response_model=UserResponse)
async def user_profile_me(request: Request) -> dict:
    """Get current authenticated user profile."""
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")
    payload = _verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = _get_user(payload["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name", user["email"]),
        "wallet_address": user.get("wallet_address"),
        "wallet_chain": user.get("wallet_chain"),
        "tier": user.get("tier", "FREE"),
        "role": user.get("role", "USER"),
        "created_at": user.get("created_at"),
        "xp": user.get("xp", 0),
        "level": user.get("level", 1),
        "badges": user.get("badges", []),
    }


@router.get("/cookie-token")
async def cookie_token(request: Request) -> JSONResponse:
    """Return user info from HttpOnly cookie. Used by frontend OAuth callback."""
    token = request.cookies.get("rmi_token")
    if not token:
        raise HTTPException(status_code=401, detail="No auth cookie")
    payload = _verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = _get_user(payload["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return JSONResponse(
        content={
            "user": {
                "id": user["id"],
                "email": user["email"],
                "display_name": user.get("display_name", user["email"]),
                "wallet_address": user.get("wallet_address"),
                "wallet_chain": user.get("wallet_chain"),
                "tier": user.get("tier", "FREE"),
                "role": user.get("role", "USER"),
                "created_at": user.get("created_at"),
            },
        }
    )


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    """Clear auth cookies (logout)."""
    response = JSONResponse(content={"status": "ok", "message": "Logged out"})
    _clear_auth_cookie(response, request)
    return response
