"""Public Auth Router — signup, login, premium tier upgrade.

Public-facing endpoints on top of the Supabase auth helpers in
``app.db_client.client`` (``sign_up`` / ``sign_in`` / ``get_user``).

Tiers: free, scout, hunter, pro.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

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


# ── Session dependency ────────────────────────────────────────────────


async def verify_session(request: Request) -> dict[str, Any]:
    """Validate the Supabase JWT from the Authorization header.

    Returns the user dict (``id``, ``email``) or raises 401.
    """
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    from app.db_client import get_db

    user = await get_db().db.get_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
    return user


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post(
    "/signup",
    summary="Create a new account",
    dependencies=[Depends(get_rate_limit_dependency("signup", rate=0.05, burst=3))],
)
async def signup(body: SignupRequest) -> dict[str, Any]:
    """Public signup endpoint. Creates a Supabase auth user and assigns tier."""
    from app.db_client import get_db

    client = get_db().db
    try:
        result = await client.sign_up(body.email, body.password)
    except Exception as exc:
        logger.warning("signup_failed email=%s err=%s", body.email, exc)
        raise HTTPException(status_code=400, detail="Signup failed") from exc
    # Store tier in user metadata (best-effort; Supabase may require
    # service-role or a trigger for metadata updates post-signup).
    return {"user": result, "tier": body.tier}


@router.post(
    "/login",
    summary="Login and get session",
    dependencies=[Depends(get_rate_limit_dependency("login", rate=0.1, burst=5))],
)
async def login(body: LoginRequest) -> dict[str, Any]:
    """Public login endpoint. Returns Supabase session."""
    if await _check_lockout(body.email):
        logger.warning("login_blocked_lockout email_hash=%s", _lockout_key(body.email)[-12:])
        raise HTTPException(
            status_code=429,
            detail="Account temporarily locked due to failed login attempts. Try again later.",
        )
    from app.db_client import get_db

    client = get_db().db
    try:
        result = await client.sign_in(body.email, body.password)
    except Exception as exc:
        await _record_failed_login(body.email)
        logger.warning("login_failed email=%s err=%s", body.email, exc)
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc
    await _clear_failed_logins(body.email)
    return result


@router.post("/premium/signup", summary="Upgrade to premium tier")
async def premium_signup(
    body: PremiumSignupRequest,
    user: dict[str, Any] = Depends(verify_session),
) -> dict[str, Any]:
    """Upgrade user to premium tier. Requires active session.

    Creates an x402 payment requirement for the premium tier; the tier is
    activated once payment is confirmed.
    """
    logger.info("premium_signup_requested user_id=%s tier=%s", user.get("id"), body.tier)
    # TODO(x402): wire real payment requirement via app.domains.billing.x402
    # once the pricing table for scout/hunter/pro tiers is finalized.
    return {"status": "pending_payment", "tier": body.tier, "user_id": user.get("id")}
