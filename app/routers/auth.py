"""Public Auth Router — signup, login, premium tier upgrade.

Public-facing endpoints on top of the Supabase auth helpers in
``app.db_client.client`` (``sign_up`` / ``sign_in`` / ``get_user``).

Tiers: free, scout, hunter, pro.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from app.config import settings
from app.core.errors import PaymentRequiredError
from app.core.logging import get_logger
from app.core.rate_limiter import get_rate_limit_dependency

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

Tier = Literal["free", "scout", "hunter", "pro"]

# ── Account lockout (audit 2026-09-06 C-1) ────────────────────────────
# 5 failed logins within a 15-minute window locks the account for 15 min.
# Counters are Redis-backed (survives restarts, shared across workers) and
# keyed on a PII-safe hash of the email, never the raw address.
LOCKOUT_MAX_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 900  # 15 min
LOCKOUT_TTL_SECONDS = 900


def _lockout_key(email: str) -> str:
    """PII-safe Redis key for the failed-login counter."""
    import hashlib
    import hmac as _hmac
    import os as _os

    salt = _os.getenv("RATE_LIMIT_SALT", "rmi-lockout-salt")
    digest = _hmac.new(salt.encode(), email.lower().encode(), hashlib.sha256).hexdigest()[:32]
    return f"rmi:auth:lockout:{digest}"


async def _check_lockout(email: str) -> bool:
    """True when the account is currently locked out."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return False
        count = await r.get(_lockout_key(email))
        return count is not None and int(count) >= LOCKOUT_MAX_ATTEMPTS
    except Exception as exc:
        logger.warning("lockout_check_failed err=%s", exc)
        return False


async def _record_failed_login(email: str) -> None:
    """Increment the failed-login counter with a sliding 15-min window."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is None:
            return
        key = _lockout_key(email)
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, LOCKOUT_WINDOW_SECONDS)
        if count >= LOCKOUT_MAX_ATTEMPTS:
            await r.expire(key, LOCKOUT_TTL_SECONDS)
            logger.warning(
                "account_locked email_hash=%s attempts=%d window_s=%d",
                _lockout_key(email)[-12:],
                count,
                LOCKOUT_WINDOW_SECONDS,
            )
    except Exception as exc:
        logger.warning("lockout_record_failed err=%s", exc)


async def _clear_failed_logins(email: str) -> None:
    """Reset the counter after a successful login."""
    try:
        from app.core.redis import get_redis_async

        r = get_redis_async()
        if r is not None:
            await r.delete(_lockout_key(email))
    except Exception as exc:
        logger.warning("lockout_clear_failed err=%s", exc)


# ── Request models ────────────────────────────────────────────────────


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    tier: Tier = "free"


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class PremiumSignupRequest(BaseModel):
    tier: Tier = "pro"


# ── Premium-tier x402 payment requirement (audit #8) ─────────────────
# Premium tier activation is gated on a coffer-facilitator-verified x402
# payment. Free/basic tier flow is unchanged (no payment required). See
# app/auth/x402_client.py — the client is FAIL-CLOSED: premium is denied
# whenever the facilitator is unreachable or rejects the payment.

#: Header carrying the verified payment id (fleet-canonical x402 replay header).
PAYMENT_ID_HEADER = "X-Payment-ID"


def _caller_key(user: dict[str, Any]) -> str:
    """Caller identity the coffer bound the payment to (authenticated user)."""
    return f"user:{user.get('id') or user.get('sub') or 'unknown'}"


def _premium_op(tier: str) -> str:
    """Server-authoritative operation label quoted/verified by the facilitator."""
    return f"premium:{tier}"


# ── httpOnly rmi_token cookie (frontend #240 coordinated) ─────────────
# The frontend (rmi-frontend 273147b3) relies on an httpOnly ``rmi_token``
# cookie for browser sessions. The backend sets it on every auth success
# path while still returning the token in the body so non-browser clients
# (Authorization header) keep working unchanged.
COOKIE_NAME = "rmi_token"


def _set_rmi_token_cookie(response: Response, token: str) -> None:
    """Set the httpOnly ``rmi_token`` cookie on an auth success response.

    ``secure`` is settings-driven: true in production, false in dev so local
    development over plain HTTP is never broken. TTL mirrors the configured
    JWT expiry (``settings.jwt_expire_minutes``).
    """
    secure = settings.environment == "prod"
    max_age = settings.jwt_expire_minutes * 60
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def _clear_rmi_token_cookie(response: Response) -> None:
    """Clear the httpOnly ``rmi_token`` cookie (logout)."""
    secure = settings.environment == "prod"
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        secure=secure,
        httponly=True,
        samesite="lax",
    )


# ── Session dependency ────────────────────────────────────────────────


async def verify_session(request: Request) -> dict[str, Any]:
    """Validate the Supabase JWT from the Authorization header.

    Returns the user dict (``id``, ``email``) or raises 401.
    """
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    from app.services.auth_db import get_auth_db

    user = await get_auth_db().db.get_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post(
    "/signup",
    summary="Create a new account",
    dependencies=[Depends(get_rate_limit_dependency("signup", rate=0.05, burst=3))],
)
async def signup(body: SignupRequest) -> Response:
    """Public signup endpoint. Creates a Supabase auth user and assigns tier."""
    from app.services.auth_db import get_auth_db

    client = get_auth_db().db
    try:
        result = await client.sign_up(body.email, body.password)
    except Exception as exc:
        logger.warning("signup_failed email=%s err=%s", body.email, exc)
        raise HTTPException(status_code=400, detail="Signup failed") from exc
    # Store tier in user metadata (best-effort; Supabase may require
    # service-role or a trigger for metadata updates post-signup).
    payload = {"user": result, "tier": body.tier}
    response = JSONResponse(content=payload)
    # Set the httpOnly cookie when signup returns a session token (email
    # confirmation may be required, in which case no token is present yet).
    token = result.get("access_token") or result.get("accessToken")
    if token:
        _set_rmi_token_cookie(response, token)
    return response


@router.post(
    "/login",
    summary="Login and get session",
    dependencies=[Depends(get_rate_limit_dependency("login", rate=0.1, burst=5))],
)
async def login(body: LoginRequest) -> Response:
    """Public login endpoint. Returns Supabase session and sets httpOnly cookie."""
    if await _check_lockout(body.email):
        logger.warning("login_blocked_lockout email_hash=%s", _lockout_key(body.email)[-12:])
        raise HTTPException(
            status_code=429,
            detail="Account temporarily locked due to failed login attempts. Try again later.",
        )
    from app.services.auth_db import get_auth_db

    client = get_auth_db().db
    try:
        result = await client.sign_in(body.email, body.password)
    except Exception as exc:
        await _record_failed_login(body.email)
        logger.warning("login_failed email=%s err=%s", body.email, exc)
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc
    await _clear_failed_logins(body.email)
    # Return the token in the body (Authorization-header path for non-browser
    # clients) AND set the httpOnly cookie for browser sessions (frontend #240).
    response = JSONResponse(content=result)
    token = result.get("access_token") or result.get("accessToken")
    if token:
        _set_rmi_token_cookie(response, token)
    return response


@router.post("/logout", summary="Logout and clear auth cookie")
async def logout() -> Response:
    """Clear the httpOnly ``rmi_token`` cookie (logout)."""
    response = JSONResponse(content={"status": "ok", "message": "Logged out"})
    _clear_rmi_token_cookie(response)
    return response


@router.post("/premium/signup", summary="Upgrade to premium tier")
async def premium_signup(
    request: Request,
    body: PremiumSignupRequest,
    user: dict[str, Any] = Depends(verify_session),
) -> dict[str, Any]:
    """Upgrade user to premium tier. Requires active session and a coffer-verified x402 payment.

    Free/basic tier requests pass through unchanged. For paid tiers (scout,
    hunter, pro) the caller must present a verified x402 payment id in the
    ``X-Payment-ID`` header; the coffer facilitator is asked to confirm the
    payment for this caller before the tier is activated. Fail-closed: an
    unreachable facilitator or rejected payment denies premium (HTTP 402).
    """
    user_id = user.get("id") or user.get("sub")
    logger.info("premium_signup_requested user_id=%s tier=%s", user_id, body.tier)

    # Free/basic tier — no payment requirement; flow unchanged.
    if body.tier == "free":
        return {"status": "active", "tier": body.tier, "user_id": user_id}

    payment_id = request.headers.get(PAYMENT_ID_HEADER, "").strip()
    if not payment_id:
        logger.info("premium_signup_no_payment user_id=%s tier=%s", user_id, body.tier)
        raise PaymentRequiredError(
            "premium tier activation requires a verified x402 payment",
            code="payment.required",
            context={"tier": body.tier, "required_header": PAYMENT_ID_HEADER},
        )

    from app.auth.x402_client import verify_payment

    result = await verify_payment(payment_id, caller_key=_caller_key(user), op=_premium_op(body.tier))
    if not result.get("verified"):
        logger.warning(
            "premium_signup_payment_denied user_id=%s tier=%s reason=%s",
            user_id,
            body.tier,
            result.get("reason"),
        )
        raise PaymentRequiredError(
            "x402 payment verification failed — premium not granted",
            code="payment.required",
            context={"tier": body.tier, "reason": result.get("reason")},
        )

    logger.info("premium_signup_activated user_id=%s tier=%s payment_id=%s", user_id, body.tier, payment_id)
    return {"status": "active", "tier": body.tier, "user_id": user_id, "payment_id": payment_id}
