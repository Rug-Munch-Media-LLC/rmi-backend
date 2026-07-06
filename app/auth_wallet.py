"""Backward-compat shim — moved to app.auth.wallet in P3B."""
from app.auth.wallet import *  # noqa: F401,F403
from app.auth.wallet import (  # noqa: F401
    decode_signature,
    get_or_create_wallet_user,
    verify_auth_token,
    verify_wallet_signature,
)
