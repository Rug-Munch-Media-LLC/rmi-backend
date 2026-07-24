"""Telegram authentication route."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

from typing import TYPE_CHECKING

from app.domains.auth.cookie import _set_auth_cookie, _set_csrf_cookie
from app.domains.auth.jwt import _create_jwt
from app.domains.auth.router import router
from app.domains.auth.store import _get_user

if TYPE_CHECKING:
    from app.domains.auth.schemas import TelegramAuthRequest


@router.post("/telegram")
async def telegram_auth(req: TelegramAuthRequest, request: Request) -> JSONResponse:
    """Authenticate via Telegram Web App data."""
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise HTTPException(status_code=500, detail="Telegram auth not configured")

    data_check = {"id": str(req.id), "auth_date": str(req.auth_date)}
    if req.first_name:
        data_check["first_name"] = req.first_name
    if req.last_name:
        data_check["last_name"] = req.last_name
    if req.username:
        data_check["username"] = req.username
    if req.photo_url:
        data_check["photo_url"] = req.photo_url

    sorted_data = "\n".join(f"{k}={v}" for k, v in sorted(data_check.items()))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected_hash = hmac.new(secret, sorted_data.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(req.hash, expected_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram auth data")

    telegram_user_id = f"tg:{req.id}"
    user = _get_user(telegram_user_id)

    if not user:
        display_name = f"{req.first_name or ''} {req.last_name or ''}".strip() or req.username or f"User{req.id}"
        email = f"{req.id}@telegram.rmi"
        user = {
            "id": telegram_user_id,
            "email": email,
            "display_name": display_name,
            "telegram_id": req.id,
            "telegram_username": req.username,
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
        r.hset("rmi:users", telegram_user_id, json.dumps(user))
        r.hset("rmi:users:telegram", str(req.id), telegram_user_id)

    jwt_token = _create_jwt(user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER"))
    body = {
        "access_token": jwt_token,
        "refresh_token": jwt_token,
        "user": {
            "id": user["id"], "email": user["email"],
            "display_name": user.get("display_name"),
            "wallet_address": None, "wallet_chain": None,
            "tier": user.get("tier", "FREE"), "role": user.get("role", "USER"),
            "created_at": user.get("created_at"),
        },
    }
    response = JSONResponse(content=body, status_code=200)
    _set_auth_cookie(response, jwt_token, request)
    _set_csrf_cookie(response, request)
    return response
