"""auth_wallet.py - DEPRECATED shim. Use app.domains.auth.wallet.

Phase 4 of AUDIT-2026-Q3.md moved this to app/domains/auth/wallet/.
This shim re-exports the public surface for legacy callers.
"""
from app.domains.auth.wallet import *  # noqa: F401,F403
from app.domains.auth.wallet import (  # noqa: F401
    decode_signature,
    get_or_create_wallet_user,
    logger,
    verify_auth_token,
    verify_wallet_signature,
)
