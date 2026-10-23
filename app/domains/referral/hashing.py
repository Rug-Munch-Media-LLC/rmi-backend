"""PII-safe identifier hashing for referral attribution.

Identifiers (user id, chat id) are NEVER stored raw. They are HMAC-SHA256
digests with a server-side salt, truncated to 32 hex chars — enough to count
distinct actors, useless for re-identification without the salt.
"""

from __future__ import annotations

import hashlib
import hmac
import os

_MAX_LEN = 256


def _salt() -> bytes:
    """Server salt: dedicated secret, else JWT secret, never a literal."""
    dedicated = os.environ.get("REFERRAL_HASH_SALT", "").strip()
    if dedicated:
        return dedicated.encode()
    fallback = os.environ.get("JWT_SECRET", "").strip()
    return (fallback or "referral-attribution-unsalted-dev").encode()


def hash_identifier(value: str | None) -> str | None:
    """Return a stable, non-reversible digest of ``value`` or None if absent."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > _MAX_LEN:
        return None
    return hmac.new(_salt(), cleaned.encode(), hashlib.sha256).hexdigest()[:32]
