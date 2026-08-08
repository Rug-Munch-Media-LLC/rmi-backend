"""Email-based authentication routes (register, login)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

from app.core.slowrate import (  # noqa: E402 — imported after logger init; kept below module header for readability
    limiter,
)
from app.domains.auth.cookie import (  # noqa: E402 — imported after logger init; kept below module header for readability
    _set_auth_cookie,
    _set_csrf_cookie,
)
from app.domains.auth.jwt import (  # noqa: E402 — imported after logger init; kept below module header for readability
    _JWT_PRIVATE_KEY,
    _JWT_PUBLIC_KEY,
    JWT_ALGORITHM,
    JWT_SECRET,
    _create_jwt,
    _derive_user_id,
)
from app.domains.auth.passwords import (  # noqa: E402 — module docstring precedes import block
    hash_password,
    verify_password,
)
from app.domains.auth.router import router  # noqa: E402 — module docstring precedes import block
from app.domains.auth.schemas import (  # noqa: E402 — runtime import for FastAPI body schemas
    EmailLoginRequest,  # noqa: TC001 — Pydantic request model, needs runtime resolution
    EmailRegisterRequest,  # noqa: TC001 — Pydantic request model, needs runtime resolution
)
from app.domains.auth.store import (  # noqa: E402 — module docstring precedes import block
    _get_user_by_email,
    _is_valid_email,
)


@router.post("/register")
@limiter.limit("5/minute")
def register_email(req: EmailRegisterRequest, request: Request) -> JSONResponse:
    """Register a new user with email/password."""
    if not _is_valid_email(req.email):
        raise HTTPException(status_code=400, detail="Invalid email format")
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    existing = _get_user_by_email(req.email)
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user_id = _derive_user_id(req.email)
    hashed = hash_password(req.password)

    user = {
        "id": user_id,
        "email": req.email,
        "display_name": req.display_name,
        "password_hash": hashed,
        "wallet_address": req.wallet_address,
        "wallet_chain": req.wallet_chain,
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
    r.hset("rmi:users:email", req.email.lower(), user_id)

    token = _create_jwt(user_id, req.email, "FREE", "USER", req.wallet_address)

    body = {
        "access_token": token,
        "refresh_token": token,
        "user": {
            "id": user_id,
            "email": req.email,
            "display_name": req.display_name,
            "wallet_address": req.wallet_address,
            "wallet_chain": req.wallet_chain,
            "tier": "FREE",
            "role": "USER",
            "created_at": user["created_at"],
        },
    }
    response = JSONResponse(content=body, status_code=200)
    _set_auth_cookie(response, token, request)
    _set_csrf_cookie(response, request)
    return response


@router.post("/login")
@limiter.limit("5/minute")
def login_email(req: EmailLoginRequest, request: Request) -> JSONResponse:
    """Login with email/password."""
    user = _get_user_by_email(req.email)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Check 2FA
    if user.get("totp_enabled") and user.get("totp_secret"):
        pre_token = _create_jwt(
            user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER"), user.get("wallet_address")
        )
        from jose import jwt as jose_jwt

        _decode_key = _JWT_PUBLIC_KEY if (JWT_ALGORITHM == "RS256" and _JWT_PUBLIC_KEY) else JWT_SECRET
        _encode_key = _JWT_PRIVATE_KEY if (JWT_ALGORITHM == "RS256" and _JWT_PRIVATE_KEY) else JWT_SECRET
        payload = jose_jwt.decode(pre_token, _decode_key, algorithms=[JWT_ALGORITHM])
        from datetime import timedelta

        payload["exp"] = datetime.now(UTC) + timedelta(minutes=5)
        payload["2fa_required"] = True
        payload["pre_auth"] = True
        pre_token = jose_jwt.encode(payload, _encode_key, algorithm=JWT_ALGORITHM)
        body = {
            "access_token": pre_token,
            "refresh_token": pre_token,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "display_name": user.get("display_name", user["email"]),
                "wallet_address": user.get("wallet_address"),
                "wallet_chain": user.get("wallet_chain"),
                "tier": user.get("tier", "FREE"),
                "role": user.get("role", "USER"),
                "created_at": user.get("created_at"),
                "_2fa_required": True,
            },
        }
        response = JSONResponse(content=body, status_code=200)
        _set_auth_cookie(response, pre_token, request)
        _set_csrf_cookie(response, request)
        return response

    token = _create_jwt(
        user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER"), user.get("wallet_address")
    )
    body = {
        "access_token": token,
        "refresh_token": token,
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
    response = JSONResponse(content=body, status_code=200)
    _set_auth_cookie(response, token, request)
    _set_csrf_cookie(response, request)
    return response
