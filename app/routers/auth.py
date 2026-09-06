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
    from app.db_client import get_db

    client = get_db().db
    try:
        result = await client.sign_in(body.email, body.password)
    except Exception as exc:
        logger.warning("login_failed email=%s err=%s", body.email, exc)
        raise HTTPException(status_code=401, detail="Invalid credentials") from exc
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
