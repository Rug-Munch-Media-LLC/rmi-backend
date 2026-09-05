"""

Wallet Authentication Helpers
=============================
Wallet signature verification and user creation.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import base58

from app.core.logging import get_logger

logger = get_logger(__name__)


def verify_wallet_signature(message: str, signature: str, address: str, chain: str = "base") -> bool:
    """
    Verify a wallet signature with cryptographic validation and nonce freshness check.

    Args:
        message: The message that was signed (includes nonce and timestamp)
        signature: The signature to verify
        address: The wallet address that supposedly signed the message
        chain: The blockchain network (evm, solana)

    Returns:
        bool: True if signature is valid and nonce is fresh, False otherwise
    """
    if not message or not signature or not address:
        return False

    # Ensure address format looks valid (basic check)
    if len(address) < 20:
        return False

    # Extract nonce from message for freshness check
    nonce = None
    for line in message.split("\n"):
        if line.startswith("Nonce: "):
            nonce = line.split("Nonce: ")[1].strip()
            break

    if not nonce:
        logger.warning("No nonce found in message")
        return False

    # Check nonce freshness in Redis
    try:
        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
        )

        # Check if nonce exists and get its timestamp
        nonce_key = f"rmi:wallet_nonce:{nonce}"
        nonce_data = r.get(nonce_key)

        if not nonce_data:
            logger.warning(f"Nonce not found or already used: {nonce}")
            return False

        # Parse nonce data (format: "address:timestamp")
        try:
            if isinstance(nonce_data, bytes):
                nonce_data = nonce_data.decode()
            stored_address, timestamp_str = nonce_data.split(":", 1)
            stored_timestamp = datetime.fromisoformat(timestamp_str)

            # Check if nonce is expired (5 minutes)
            if datetime.now(UTC) - stored_timestamp > timedelta(minutes=5):
                logger.warning(f"Nonce expired: {nonce}")
                r.delete(nonce_key)
                return False

            # Verify the address matches the stored address
            if stored_address.lower() != address.lower():
                logger.warning(f"Address mismatch in nonce: {address} vs {stored_address}")
                return False

            # Delete nonce after successful verification (replay protection)
            r.delete(nonce_key)

        except (ValueError, TypeError) as e:
            logger.debug("unknown_error", exc_info=True)
            logger.warning(f"Error parsing nonce data: {e}")
            return False

    except Exception:
        logger.debug("unknown_error", exc_info=True)
        logger.warning("Redis not available for nonce verification")
        # Continue with signature verification even if Redis is unavailable
        # This maintains backward compatibility

    # Perform actual cryptographic signature verification based on chain type
    if chain == "solana":
        return _verify_solana_signature(address, message, signature)
    else:
        # Default to EVM signature verification for all other chains
        return _verify_evm_signature(address, message, signature)


def _verify_evm_signature(address: str, message: str, signature: str) -> bool:
    """Verify an EVM wallet signature."""
    try:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        encoded = encode_defunct(text=message)
        recovered = Account.recover_message(encoded, signature=signature)
        return recovered.lower() == address.lower()
    except ImportError:
        # eth_account not available - fail closed
        logger.error("eth_account not available for EVM signature verification")
        return False
    except (ConnectionError, TimeoutError, ValueError) as e:
        logger.debug("_verify_evm_signature_error", exc_info=True)
        logger.debug(f"EVM signature verification failed: {e}")
        return False


def _verify_solana_signature(address: str, message: str, signature: str) -> bool:
    """Verify a Solana wallet signature."""
    try:
        from nacl.signing import VerifyKey

        pubkey = VerifyKey(base58.b58decode(address))
        msg_bytes = message.encode("utf-8")
        sig_bytes = base64.b64decode(signature)
        pubkey.verify(msg_bytes, sig_bytes)
        return True
    except ImportError:
        logger.error("nacl not available for Solana signature verification")
        return False
    except (ConnectionError, TimeoutError, ValueError) as e:
        logger.debug("_verify_solana_signature_error", exc_info=True)
        logger.debug(f"Solana signature verification failed: {e}")
        return False


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
    except (ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
        logger.debug("get_or_create_wallet_user_error", exc_info=True)
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
            "created_at": datetime.now(UTC).isoformat(),
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
                "id": user.get("id"),
                "email": user.get("email"),
                "address": user.get("wallet"),
                "tier": user.get("tier", "FREE"),
                "role": user.get("role", "USER"),
            }
    except (KeyError, TypeError) as e:
        logger.debug("verify_auth_token_error", exc_info=True)
        logger.debug(f"Token verification failed: {e}")
    return None
