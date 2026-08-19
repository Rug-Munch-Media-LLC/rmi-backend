"""Auth compat router — frontend-facing aliases for the auth domain.

The deployed rmi-frontend calls these paths (verified from the production
bundle 2026-08-19):

    /api/v1/auth/me                → GET  user_profile_me
    /api/v1/auth/profile           → GET  user_profile_me
    /api/v1/auth/wallet            → POST wallet auth (nonce/verify)
    /api/v1/auth/change-password   → POST change password
    /api/v1/auth/delete-account    → POST delete account
    /api/v1/auth/privacy-settings  → GET/PUT privacy settings

The canonical auth domain router (app.domains.auth.router) is mounted at
/api/v1/auth and serves /register, /login, /user/me, /cookie-token,
/logout, /telegram. This module adds the missing aliases so the consumer
site's auth flows work without a frontend rebuild.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.core.logging import get_logger
from app.domains.auth.jwt import _create_jwt, _verify_jwt
from app.domains.auth.passwords import hash_password, verify_password
from app.domains.auth.store import _get_user, _get_user_by_email, _save_user
from app.domains.auth.wallet import get_or_create_wallet_user, verify_wallet_signature

log = get_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth-compat"])


def _extract_token(request: Request) -> str | None:
    """Pull bearer token from Authorization header or rmi_token cookie."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.cookies.get("rmi_token")


def _require_user(request: Request) -> dict[str, Any]:
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=401, detail="Missing auth token")
    payload = _verify_jwt(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = _get_user(payload["id"])
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/me")
async def auth_me(request: Request) -> dict[str, Any]:
    """Alias for GET /user/me — frontend calls /api/v1/auth/me."""
    user = _require_user(request)
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


@router.get("/profile")
async def auth_profile(request: Request) -> dict[str, Any]:
    """Alias for GET /user/me — frontend calls /api/v1/auth/profile."""
    return await auth_me(request)


@router.post("/wallet")
async def auth_wallet(request: Request) -> dict[str, Any]:
    """Wallet auth — verify signature and return tokens.

    Body: {"address": "...", "signature": "...", "message": "...", "chain": "base"}
    """
    body = await request.json()
    address = (body.get("address") or "").strip()
    signature = (body.get("signature") or "").strip()
    message = (body.get("message") or "").strip()
    chain = (body.get("chain") or "base").strip().lower()

    if not address or not signature or not message:
        raise HTTPException(status_code=422, detail="address, signature and message are required")

    if not verify_wallet_signature(message, signature, address, chain=chain):
        raise HTTPException(status_code=401, detail="Invalid wallet signature")

    result = await get_or_create_wallet_user(address, chain=chain)
    return result


@router.post("/change-password")
async def auth_change_password(request: Request) -> dict[str, Any]:
    """Change password for the authenticated user.

    Body: {"current_password": "...", "new_password": "..."}
    """
    user = _require_user(request)
    body = await request.json()
    current = (body.get("current_password") or "").strip()
    new_password = (body.get("new_password") or "").strip()

    if not current or not new_password:
        raise HTTPException(status_code=422, detail="current_password and new_password are required")
    if len(new_password) < 8:
        raise HTTPException(status_code=422, detail="new_password must be at least 8 characters")

    stored = user.get("password_hash") or user.get("password")
    if stored and not verify_password(current, stored):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    user["password_hash"] = hash_password(new_password)
    _save_user(user)
    log.info("auth_password_changed user=%s", user["id"])
    return {"ok": True}


@router.post("/delete-account")
async def auth_delete_account(request: Request) -> dict[str, Any]:
    """Delete the authenticated user's account."""
    user = _require_user(request)
    try:
        import redis
        import json
        import os

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
        r.delete(f"rmi:users:{user['id']}")
        r.delete(f"rmi:user:{user['id']}")
        log.info("auth_account_deleted user=%s", user["id"])
    except Exception as exc:  # noqa: BLE001 — best-effort deletion
        log.warning("auth_delete_account_redis_failed user=%s err=%s", user["id"], exc)
    return {"ok": True}


@router.get("/privacy-settings")
async def auth_privacy_settings(request: Request) -> dict[str, Any]:
    """Get privacy settings for the authenticated user."""
    user = _require_user(request)
    return {
        "email_visible": user.get("privacy", {}).get("email_visible", False),
        "wallet_visible": user.get("privacy", {}).get("wallet_visible", False),
        "activity_visible": user.get("privacy", {}).get("activity_visible", True),
    }


@router.put("/privacy-settings")
async def auth_privacy_settings_update(request: Request) -> dict[str, Any]:
    """Update privacy settings for the authenticated user."""
    user = _require_user(request)
    body = await request.json()
    privacy = user.get("privacy") or {}
    for key in ("email_visible", "wallet_visible", "activity_visible"):
        if key in body:
            privacy[key] = bool(body[key])
    user["privacy"] = privacy
    _save_user(user)
    return {"ok": True, "privacy": privacy}
