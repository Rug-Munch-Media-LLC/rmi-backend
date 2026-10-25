"""Webhook authentication — fail-closed HMAC + auth-header verification.

Unverifiable requests are rejected, never accepted by default.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from app.databus.webhook_config import _WEBHOOK_ERRORS, logger


def _verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """HMAC signature verification (fail-closed).

    Returns False when the secret or signature is missing so callers reject
    unverifiable webhooks instead of accepting them.
    """
    if not secret or not signature:
        return False
    try:
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except _WEBHOOK_ERRORS as exc:
        logger.warning("webhook_signature_check_failed", error=str(exc))
        return False


def _verify_auth_header(headers: dict[str, Any], auth_env: str) -> bool:
    """Check the Bearer/raw token some services send alongside the signature.

    Helius sends its secret as an ``authHeader`` Bearer token, so the signature
    header alone is not proof. Missing expected token => nothing to check.
    """
    expected_token = os.getenv(auth_env, "")
    if not expected_token:
        return True
    # Starlette lowercases header keys in dict(request.headers)
    auth_header = headers.get("authorization") or headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    return token == expected_token
