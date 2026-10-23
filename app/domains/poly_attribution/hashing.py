"""Salted hashing for wallet / builder-code attribution keys.

No raw PII is ever persisted. The salt is mandatory in production: an
unsalted hash of an address is trivially reversible by enumeration.
"""

from __future__ import annotations

import hashlib
import os


class MissingSaltError(RuntimeError):
    """Raised when the attribution salt is not configured."""


def hash_identifier(value: str) -> str:
    """Return ``sha256(salt|value.lower())`` as hex, raising if unsalted."""
    salt = os.getenv("POLY_ATTRIBUTION_SALT")
    if not salt:
        raise MissingSaltError("POLY_ATTRIBUTION_SALT is not set")
    digest = hashlib.sha256(f"{salt}|{value.strip().lower()}".encode())
    return digest.hexdigest()
