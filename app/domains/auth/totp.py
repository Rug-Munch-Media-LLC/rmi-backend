"""TOTP/2FA helpers and API endpoints."""

import base64
import io
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

from app.domains.auth.router import router
from app.domains.auth.jwt import JWT_ALGORITHM, JWT_SECRET, _JWT_PRIVATE_KEY, _JWT_PUBLIC_KEY, _create_jwt
from app.domains.auth.store import _get_user, _get_user_by_email, _save_user
from app.domains.auth.passwords import hash_password, verify_password
from app.domains.auth.cookie import _set_auth_cookie, _set_csrf_cookie
from app.domains.auth.deps import get_current_user
from app.domains.auth.schemas import TwoFAEnableRequest, TwoFALoginRequest, TwoFASetupResponse, TwoFAStatusResponse, TwoFAVerifyRequest
from app.core.slowrate import limiter

# ── 2FA / TOTP ──
try:
    import pyotp
    import qrcode

    PYOTP_AVAILABLE = True
except ImportError:
    PYOTP_AVAILABLE = False
    logger.warning("pyotp not installed - 2FA endpoints will return 503")

TOTP_ISSUER = os.getenv("TOTP_ISSUER", "RugMunch Intelligence")
TOTP_DIGITS = 6
TOTP_INTERVAL = 30
TOTP_WINDOW = 1


def _generate_totp_secret() -> str:
    if not PYOTP_AVAILABLE:
        raise RuntimeError("pyotp not installed")
    return pyotp.random_base32()


def _build_totp(secret: str) -> Any:
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)


def _verify_totp(secret: str, code: str) -> bool:
    if not PYOTP_AVAILABLE or not secret or not code:
        return False
    totp = _build_totp(secret)
    return totp.verify(code, valid_window=TOTP_WINDOW)


def _get_totp_uri(secret: str, email: str) -> str:
    totp = _build_totp(secret)
    return totp.provisioning_uri(name=email, issuer_name=TOTP_ISSUER)


def _generate_qr_base64(uri: str) -> str:
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _generate_backup_codes(count: int = 8) -> list[str]:
    codes = []
    for _ in range(count):
        raw = secrets.randbelow(10_000_000_000)
        code = f"{raw:010d}"[:8]
        codes.append(f"{code[:4]}-{code[4:]}")
    return codes


def _hash_backup_code(code: str) -> str:
    return hash_password(code)


def _verify_backup_code(code: str, hashed: str) -> bool:
    return verify_password(code, hashed)


# ── 2FA Endpoints ──

@router.post("/2fa/setup")
async def twofa_setup(request: Request) -> dict[str, Any]:
    if not PYOTP_AVAILABLE:
        raise HTTPException(status_code=503, detail="2FA service unavailable")
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user.get("totp_secret"):
        raise HTTPException(status_code=400, detail="2FA already enabled. Disable first to reconfigure.")
    secret = _generate_totp_secret()
    uri = _get_totp_uri(secret, user["email"])
    qr_b64 = _generate_qr_base64(uri)
    backup_codes = _generate_backup_codes(8)
    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    pending = {
        "secret": secret,
        "backup_codes": [_hash_backup_code(c) for c in backup_codes],
        "created_at": datetime.now(UTC).isoformat(),
    }
    r.setex(f"rmi:2fa_pending:{user['id']}", timedelta(minutes=10), json.dumps(pending))
    return {"secret": secret, "qr_code": qr_b64, "uri": uri, "backup_codes": backup_codes}


@router.post("/2fa/enable")
async def twofa_enable(req: TwoFAEnableRequest, request: Request) -> dict[str, Any]:
    if not PYOTP_AVAILABLE:
        raise HTTPException(status_code=503, detail="2FA service unavailable")
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    r = get_redis()
    if r is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")
    pending_raw = r.get(f"rmi:2fa_pending:{user['id']}")
    if not pending_raw:
        raise HTTPException(status_code=400, detail="No pending 2FA setup. Call /2fa/setup first.")
    pending = json.loads(pending_raw)
    secret = pending["secret"]
    if not _verify_totp(secret, req.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code. Check your authenticator app time sync.")
    user["totp_secret"] = secret
    user["totp_enabled"] = True
    user["totp_created_at"] = pending["created_at"]
    user["totp_backup_codes"] = pending["backup_codes"]
    user["totp_last_used"] = None
    _save_user(user)
    r.delete(f"rmi:2fa_pending:{user['id']}")
    logger.info(f"[2FA ENABLED] user={user['id']} email={user['email']}")
    return {"status": "ok", "message": "2FA enabled successfully", "method": "totp"}


@router.post("/2fa/disable")
async def twofa_disable(request: Request) -> dict[str, Any]:
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not user.get("totp_enabled"):
        raise HTTPException(status_code=400, detail="2FA is not enabled")
    for key in ["totp_secret", "totp_enabled", "totp_created_at", "totp_backup_codes", "totp_last_used"]:
        user.pop(key, None)
    _save_user(user)
    logger.info(f"[2FA DISABLED] user={user['id']} email={user['email']}")
    return {"status": "ok", "message": "2FA disabled successfully"}


@router.get("/2fa/status", response_model=TwoFAStatusResponse)
async def twofa_status(request: Request) -> dict[str, Any]:
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    enabled = user.get("totp_enabled", False)
    return {
        "enabled": enabled,
        "method": "totp" if enabled else "none",
        "created_at": user.get("totp_created_at"),
        "last_used": user.get("totp_last_used"),
    }


@router.post("/2fa/verify")
async def twofa_verify(req: TwoFAVerifyRequest, request: Request) -> dict[str, Any]:
    if not PYOTP_AVAILABLE:
        raise HTTPException(status_code=503, detail="2FA service unavailable")
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    if not user.get("totp_enabled") or not user.get("totp_secret"):
        raise HTTPException(status_code=400, detail="2FA not enabled")
    if not _verify_totp(user["totp_secret"], req.code):
        raise HTTPException(status_code=400, detail="Invalid TOTP code")
    user["totp_last_used"] = datetime.now(UTC).isoformat()
    _save_user(user)
    return {"status": "ok", "verified": True}


@router.post("/2fa/login")
async def twofa_login(req: TwoFALoginRequest, request: Request) -> JSONResponse:
    if not PYOTP_AVAILABLE:
        raise HTTPException(status_code=503, detail="2FA service unavailable")
    user = _get_user_by_email(req.email)
    if not user or not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.get("totp_enabled") or not user.get("totp_secret"):
        raise HTTPException(status_code=400, detail="2FA not enabled for this account. Use /login instead.")
    code = req.code.strip().replace("-", "")
    if _verify_totp(user["totp_secret"], code):
        pass
    else:
        backup_codes = user.get("totp_backup_codes", [])
        matched = False
        for i, hashed in enumerate(backup_codes):
            if _verify_backup_code(req.code, hashed):
                matched = True
                backup_codes.pop(i)
                user["totp_backup_codes"] = backup_codes
                break
        if not matched:
            raise HTTPException(status_code=401, detail="Invalid 2FA code or backup code")
    user["totp_last_used"] = datetime.now(UTC).isoformat()
    _save_user(user)
    token = _create_jwt(user["id"], user["email"], user.get("tier", "FREE"), user.get("role", "USER"), user.get("wallet_address"))
    logger.info(f"[2FA LOGIN] user={user['id']} email={user['email']}")
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
