"""Shared JWT Validation Library — used by all RMI services to validate tokens.

This is the canonical JWT validation module. Every RMI service (walletpress,
pryscraper, degenfeed-web) imports this to verify tokens issued by rmi-backend.

Key design decisions:
- RS256 asymmetric signing (private key on rmi-backend, public key shared)
- Falls back to HS256 for backward compatibility
- Supports both ``Authorization: Bearer <token>`` and HttpOnly cookie extraction
- JWKS endpoint for standard key distribution

Usage:
    from app.auth.jwt_validation import validate_token, get_jwks

    try:
        claims = validate_token("eyJ...")
        # claims = {"id": "...", "email": "...", "tier": "FREE", ...}
    except HTTPException as exc:
        # 401 if invalid/expired
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────
# Services configure these via environment variables.
# rmi-backend's app.config.settings is the primary source of truth.

_JWT_ALGORITHM: str = os.getenv("RMI_JWT_ALGORITHM", "RS256")
_JWT_SECRET: str = os.getenv("RMI_JWT_SECRET", "")
_JWT_PUBLIC_KEY: str = os.getenv("RMI_JWT_PUBLIC_KEY", "")
_JWT_PRIVATE_KEY: str = os.getenv("RMI_JWT_PRIVATE_KEY", "")

# Cache for loaded keys
_public_key: str | None = None
_private_key: str | None = None

def _load_key(key_or_path: str) -> str:
    """Load a PEM key from a file path, or return the PEM string directly."""
    if os.path.exists(key_or_path) and os.path.isfile(key_or_path):
        with open(key_or_path) as _f:
            return _f.read()
    return key_or_path

def _get_public_key() -> str | None:
    """Get the public key (RS256) or secret (HS256)."""
    global _public_key
    if _public_key is not None:
        return _public_key

    if _JWT_ALGORITHM == "RS256":
        raw = _JWT_PUBLIC_KEY
        if raw:
            _public_key = _load_key(raw)
            return _public_key
        # Fallback: derive from private key config (for services that only have the private key)
        raw_pk = _JWT_PRIVATE_KEY
        if raw_pk:
            pem = _load_key(raw_pk)
            if pem and "PRIVATE KEY" in pem:
                logger.warning(
                    "Using JWT_PRIVATE_KEY for verification — set JWT_PUBLIC_KEY for proper RS256"
                )
                _public_key = pem
                return _public_key
        return None

    # HS256 fallback
    _public_key = _JWT_SECRET
    return _public_key

def _get_private_key() -> str | None:
    """Get the private key for RS256 signing, or secret for HS256."""
    global _private_key
    if _private_key is not None:
        return _private_key

    if _JWT_ALGORITHM == "RS256":
        raw = _JWT_PRIVATE_KEY
        if raw:
            _private_key = _load_key(raw)
            return _private_key
        return None

    _private_key = _JWT_SECRET
    return _private_key

def validate_token(token: str) -> dict[str, Any]:
    """Validate a JWT and return user claims.

    Args:
        token: The JWT string (without 'Bearer ' prefix).

    Returns:
        Dict with keys: id, email, tier, role, wallet (optional).

    Raises:
        HTTPException(401): If token is invalid, expired, or malformed.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Missing authentication token")

    key = _get_public_key()
    if not key:
        logger.error("No JWT public key or secret configured for validation")
        raise HTTPException(status_code=500, detail="Authentication misconfigured")

    algorithm = "RS256" if _JWT_ALGORITHM == "RS256" else "HS256"
    try:
        payload = jwt.decode(token, key, algorithms=[algorithm])
    except JWTError as exc:
        logger.warning("JWT validation failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token") from exc

    # Extract standard claims
    claims: dict[str, Any] = {
        "id": payload.get("sub", ""),
        "email": payload.get("email", ""),
        "tier": payload.get("tier", "FREE"),
        "role": payload.get("role", "USER"),
        "wallet": payload.get("wallet"),
    }

    if not claims["id"]:
        raise HTTPException(status_code=401, detail="Token missing subject (sub)")

    return claims

def create_token(
    user_id: str,
    email: str,
    tier: str = "FREE",
    role: str = "USER",
    wallet: str | None = None,
    expire_days: int = 7,
) -> str:
    """Create a signed JWT. Primarily used by rmi-backend.

    Args:
        user_id: Unique user identifier.
        email: User email address.
        tier: Subscription tier (FREE, PRO, ENTERPRISE).
        role: User role (USER, ADMIN, MODERATOR).
        wallet: Optional wallet address.
        expire_days: Token lifetime in days.

    Returns:
        Encoded JWT string.
    """
    key = _get_private_key()
    if not key:
        raise RuntimeError("No JWT private key or secret configured for signing")

    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "email": email,
        "tier": tier,
        "role": role,
        "iat": now,
        "exp": now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=expire_days),
    }
    if wallet:
        payload["wallet"] = wallet

    algorithm = "RS256" if _JWT_ALGORITHM == "RS256" else "HS256"
    if algorithm == "RS256":
        return jwt.encode(payload, key, algorithm="RS256")
    return jwt.encode(payload, key, algorithm="HS256")

def extract_token_from_request(request: Request) -> str | None:
    """Extract JWT from request: checks HttpOnly cookie first, then Authorization header.

    This is the canonical extraction order used by all RMI services:
    1. ``rmi_token`` cookie (browser clients with HttpOnly cookie)
    2. ``Authorization: Bearer <token>`` header (API clients)

    Args:
        request: FastAPI Request object.

    Returns:
        The raw JWT string, or None if not found.
    """
    # 1. Check HttpOnly cookie (browser clients)
    cookie_token = request.cookies.get("rmi_token")
    if cookie_token:
        return cookie_token

    # 2. Fall back to Authorization header (API clients)
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None

async def get_current_user(request: Request) -> dict[str, Any] | None:
    """Verify JWT and return user claims, or None if not authenticated.

    This is a FastAPI dependency-compatible function that returns None
    instead of raising (for optional auth endpoints).
    """
    token = extract_token_from_request(request)
    if not token:
        return None
    try:
        return validate_token(token)
    except HTTPException:
        return None

async def require_auth(request: Request) -> dict[str, Any]:
    """Require valid JWT. Raises 401 if missing or invalid.

    This is a FastAPI dependency for protected endpoints.
    """
    token = extract_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required")
    return validate_token(token)

# ── JWKS endpoint support ─────────────────────────────────────────────────────

def get_jwks() -> dict[str, Any]:
    """Return a JWKS (JSON Web Key Set) for RS256 public key distribution.

    Services can fetch this from rmi-backend at ``/.well-known/jwks.json``
    to dynamically discover the public key for JWT validation.

    For HS256 (symmetric), we return an error — symmetric keys cannot be
    safely distributed via JWKS.
    """
    if _JWT_ALGORITHM != "RS256":
        return {"error": "JWKS only available for RS256", "algorithm": _JWT_ALGORITHM}

    pub_key = _get_public_key()
    if not pub_key:
        return {"error": "No public key configured"}

    # Extract modulus (n) and exponent (e) from PEM
    # This is a simplified approach; for production use python-jose's RS256 key loading
    try:
        from jose.backends.rsa_backend import RSAKey
        from jose.constants import Algorithms

        rsa_key = RSAKey(pub_key, algorithm=Algorithms.RS256)
        jwk = rsa_key.to_dict()
        return {"keys": [jwk]}
    except ImportError:
        # Fallback: return PEM as a non-standard field
        return {
            "keys": [],
            "pem": pub_key,
            "note": "Full JWKS requires python-jose RSAKey — using raw PEM fallback",
        }

# ══════════════════════════════════════════════════════════════════════════════
# WARNING: Don't silently import timedelta at module level — it's only needed
# in create_token. Imported here to keep the function self-contained.
# ══════════════════════════════════════════════════════════════════════════════
from datetime import timedelta  # noqa: E402 — required by create_token
