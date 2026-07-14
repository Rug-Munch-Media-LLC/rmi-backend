"""
Wallet Authentication Helpers
=============================
Wallet signature verification and user creation.
"""

from app.core.logging import get_logger
import os
from datetime import datetime
from typing import Any

logger = get_logger(__name__)


def verify_wallet_signature(message: str, signature: str, address: str) -> bool:
    """
    Verify a wallet signature.

    NOTE: This is a validation stub. In production, use a proper signing library
    (e.g., web3.py for Ethereum, @solana/web3.js for Solana) to verify signatures.

    For now, returns True if all fields are non-empty (basic validation).
    """
    if not message or not signature or not address:
        return False

    # Ensure address format looks valid (basic check)
    if len(address) < 20:
        return False

    # Basic signature length check
    # Typical sig is 65 hex chars for EVM
    return len(signature) >= 60


def decode_signature(signature: str) -> tuple[str, str, int]:
    """
    Decode a wallet signature into r, s, v components.

    Returns (r_hex, s_hex, v_int) for signature verification.
    """
    import binascii

    sig_bytes = binascii.unhexlify(signature.replace("0x", ""))

    r = sig_bytes[:32].hex()
    s = sig_bytes[32:64].hex()
    v = sig_bytes[64]

    return r, s, v


async def get_or_create_wallet_user(address: str, chain: str = "base") -> dict[str, Any]:
    """
    Get or create a user based on wallet address.

    Returns dict with:
        - access_token
        - refresh_token
        - id
        - email
        - display_name
        - tier
        - role
        - created_at
    """
    import hashlib
    import json

    # Derive user_id from wallet address
    user_id = hashlib.sha256(address.lower().encode()).hexdigest()[:32]

    # Try to load existing user
    r = None
    try:
        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )
    except Exception as e:
        logger.warning(f"Redis not available for wallet user lookup: {e}")

    user = None
    if r:
        data = r.hget("rmi:wallet_users", address.lower())
        if data:
            user = json.loads(data)

    # Create new user if doesn't exist
    if not user:
        # Generate fake email for wallet users (no email required for wallet auth)
        email = f"{address.lower()}@wallet.rmi"
        display_name = f"Wallet User {address[2:8].upper()}"

        user = {
            "id": user_id,
            "address": address.lower(),
            "email": email,
            "display_name": display_name,
            "chain": chain,
            "tier": "FREE",
            "role": "USER",
            "created_at": datetime.utcnow().isoformat(),
            "xp": 0,
            "level": 1,
            "badges": [],
            "scans_remaining": 5,
            "scans_used": 0,
        }

        if r:
            r.hset("rmi:wallet_users", address.lower(), json.dumps(user))
            # Also store in main users hash
            r.hset("rmi:users", user_id, json.dumps(user))

    # Generate JWT token
    from app.domains.auth import _create_jwt

    # Use email if available, otherwise derive from address
    email = user.get("email") or f"{address.lower()}@wallet.rmi"
    token = _create_jwt(user_id, email, user.get("tier", "FREE"), user.get("role", "USER"), address)

    return {
        "access_token": token,
        "refresh_token": token,
        "id": user_id,
        "email": email,
        "display_name": user.get("display_name", display_name),
        "tier": user.get("tier", "FREE"),
        "role": user.get("role", "USER"),
        "created_at": user.get("created_at"),
        "address": address,
        "chain": chain,
    }


async def verify_auth_token(token: str) -> dict[str, Any] | None:
    """
    Verify a JWT token and return user info.

    Returns:
        Dict with user info if valid, None if invalid/expired
    """
    from app.domains.auth import _verify_jwt

    try:
        user = _verify_jwt(token)
        if user:
            # Return user info in expected format
            return {
                "id": user.get("user_id"),
                "email": user.get("email"),
                "address": user.get("wallet_address"),
                "tier": user.get("tier", "FREE"),
                "role": user.get("role", "USER"),
            }
    except Exception as e:
        logger.debug(f"Token verification failed: {e}")
    return None
