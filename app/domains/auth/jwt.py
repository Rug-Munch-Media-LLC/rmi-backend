"""JWT token creation, verification, and helpers."""

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
            raise RuntimeError(
                "JWT_ALGORITHM=RS256 configured but JWT_PRIVATE_KEY or JWT_PUBLIC_KEY not set."
            )
        else:
            logger.warning(
                "JWT_ALGORITHM=RS256 configured but keys not set. Falling back to HS256."
            )
            JWT_ALGORITHM = "HS256"


def _create_jwt(
    user_id: str, email: str, tier: str = "FREE", role: str = "USER", wallet: str | None = None
) -> str:
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


def _verify_jwt(token: str) -> dict[str, Any] | None:
    try:
        if JWT_ALGORITHM == "RS256" and _JWT_PUBLIC_KEY:
            payload = jwt.decode(token, _JWT_PUBLIC_KEY, algorithms=["RS256"])
        else:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        # Guard against malformed tokens lacking required claims
        if "sub" not in payload:
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


def _derive_user_id(email: str) -> str:
    return hashlib.sha256(email.lower().encode()).hexdigest()[:32]


def generate_nonce() -> str:
    return secrets.token_urlsafe(16)
