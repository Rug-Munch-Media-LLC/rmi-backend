"""JWT token creation, verification, and helpers."""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt

from app.config import settings as _settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Config ──
JWT_SECRET = _settings.jwt_secret
JWT_ALGORITHM = _settings.jwt_algorithm
JWT_EXPIRY_DAYS = 7


def _load_key(key_or_path: str) -> str:
    """Load a PEM key from a file path, or return the PEM string directly."""
    if os.path.exists(key_or_path) and os.path.isfile(key_or_path):
        with open(key_or_path) as _f:
            return _f.read()
    return key_or_path


# RS256 key loading
_JWT_PRIVATE_KEY: str | None = None
_JWT_PUBLIC_KEY: str | None = None

if JWT_ALGORITHM == "RS256":
    _raw_private = _settings.jwt_private_key
    _raw_public = _settings.jwt_public_key
    if _raw_private and _raw_public:
        _JWT_PRIVATE_KEY = _load_key(_raw_private)
        _JWT_PUBLIC_KEY = _load_key(_raw_public)
    else:
        if _settings.environment == "prod":
            raise RuntimeError("JWT_ALGORITHM=RS256 configured but JWT_PRIVATE_KEY or JWT_PUBLIC_KEY not set.")
        else:
            logger.warning("JWT_ALGORITHM=RS256 configured but keys not set. Falling back to HS256.")
            JWT_ALGORITHM = "HS256"


def _create_jwt(user_id: str, email: str, tier: str = "FREE", role: str = "USER", wallet: str | None = None) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "email": email,
        "tier": tier,
        "role": role,
        "iat": now,
        "exp": now + timedelta(days=JWT_EXPIRY_DAYS),
    }
    if wallet:
        payload["wallet"] = wallet
    if JWT_ALGORITHM == "RS256" and _JWT_PRIVATE_KEY:
        return jwt.encode(payload, _JWT_PRIVATE_KEY, algorithm="RS256")
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _verify_jwt(token: str, *, allow_preauth: bool = False) -> dict[str, Any] | None:
    """Verify a JWT and return user claims.

    By default, pending-2FA tokens (carrying ``pre_auth`` / ``2fa_required``
    claims, issued by the email login flow) are REJECTED — they are not fully
    authenticated and must never unlock full-auth routes. The 2FA-completion
    endpoint (``POST /2fa/verify``) passes ``allow_preauth=True`` explicitly.

    Args:
        token: The JWT string.
        allow_preauth: Allow pending-2FA tokens (2FA-completion endpoint only).
    """
    try:
        if JWT_ALGORITHM == "RS256" and _JWT_PUBLIC_KEY:
            payload = jwt.decode(token, _JWT_PUBLIC_KEY, algorithms=["RS256"])
        else:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        # Guard against malformed tokens lacking required claims
        if "sub" not in payload:
            return None
        # AUDIT-2026-Q3 (2FA bypass): a token with pre_auth / 2fa_required is a
        # pending-2FA credential. Reject it on full-auth routes unless the
        # caller explicitly opts in (2FA-completion endpoint only).
        if not allow_preauth and (payload.get("pre_auth") or payload.get("2fa_required")):
            logger.warning("preauth_token_rejected")
            return None
        return {
            "id": payload["sub"],
            "email": payload.get("email", ""),
            "tier": payload.get("tier", "FREE"),
            "role": payload.get("role", "USER"),
            "wallet": payload.get("wallet"),
        }
    except JWTError:
        return None


def is_preauth_token(token: str) -> bool:
    """Return True if the token is a validly-signed pending-2FA (pre_auth) token.

    Used by the auth middleware's optional-auth path: a pre-auth token is a
    *legitimate* credential that simply hasn't completed 2FA, so it must not
    be rejected with 401 on public routes — but it grants no user claims and
    is never accepted as full auth (see _verify_jwt).
    """
    if not token:
        return False
    try:
        if JWT_ALGORITHM == "RS256" and _JWT_PUBLIC_KEY:
            payload = jwt.decode(token, _JWT_PUBLIC_KEY, algorithms=["RS256"])
        else:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except JWTError:
        return False
    return bool(payload.get("pre_auth") or payload.get("2fa_required"))


def _derive_user_id(email: str) -> str:
    return hashlib.sha256(email.lower().encode()).hexdigest()[:32]


def generate_nonce() -> str:
    return secrets.token_urlsafe(16)
