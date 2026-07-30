"""
Web3 Chat Utilities
===================
Utility functions for address validation, formatting, parsing, and command parsing.
"""

from __future__ import annotations

import hashlib
import time
import uuid
from decimal import Decimal
from typing import Any

from app.services.web3_chat.models import SUPPORTED_CHAINS


def validate_address(address: str, chain: str) -> bool:
    """Basic address validation for supported chains."""
    if not address or len(address) < 10:
        return False
    if chain == "solana":
        return len(address) >= 32 and address.startswith(
            ("D", "G", "H", "J", "B", "F", "A", "5", "3", "2", "4", "6", "7", "8", "9")
        )
    if chain == "lightning":
        return address.startswith(("lnbc", "lntb", "bc1", "lnurl"))
    # EVM chains
    return address.startswith("0x") and len(address) == 42


def format_tx_link(tx_hash: str, chain: str) -> str:
    """Format a transaction link for the given chain."""
    base_url = SUPPORTED_CHAINS.get(chain, {}).get("explorer", "")
    if not base_url or not tx_hash:
        return ""
    return f"{base_url}{tx_hash}"


def parse_amount(amount_str: str, decimals: int = 18) -> Decimal:
    """Parse a human-readable amount string to Decimal."""
    try:
        return Decimal(str(amount_str))
    except (ValueError, ArithmeticError):
        raise ValueError(f"Invalid amount: {amount_str}") from None


def generate_nonce(user_id: str) -> str:
    """Generate a unique nonce for wallet signatures."""
    raw = f"{user_id}:{uuid.uuid4().hex}:{time.time()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def verify_payment_command(content: str) -> dict[str, Any] | None:
    """Parse a /pay command from chat message content.

    Returns parsed data if valid, None if not a payment command.
    """
    content = content.strip()

    # /pay @user 0.1 ETH
    # /pay @user 500 sats
    # /pay @user 10 USDC
    # /pay @user 0.5 SOL
    if content.startswith("/pay"):
        parts = content.split()
        if len(parts) < 4:
            return {"error": "Usage: /pay @username amount TOKEN"}
        recipient = parts[1].lstrip("@")
        amount = parts[2]
        token = parts[3].upper()
        return {
            "command": "pay",
            "recipient": recipient,
            "amount": amount,
            "token": token,
        }

    # /tip @user 0.1 ETH
    if content.startswith("/tip"):
        parts = content.split()
        if len(parts) < 4:
            return {"error": "Usage: /tip @username amount TOKEN"}
        recipient = parts[1].lstrip("@")
        amount = parts[2]
        token = parts[3].upper()
        return {
            "command": "tip",
            "recipient": recipient,
            "amount": amount,
            "token": token,
        }

    # /request 0.1 ETH @user [reason]
    if content.startswith("/request"):
        parts = content.split()
        if len(parts) < 4:
            return {"error": "Usage: /request amount TOKEN @username [reason]"}
        amount = parts[1]
        token = parts[2].upper()
        recipient = parts[3].lstrip("@")
        reason = " ".join(parts[4:]) if len(parts) > 4 else None
        return {
            "command": "request",
            "recipient": recipient,
            "amount": amount,
            "token": token,
            "reason": reason,
        }

    # /split 1 ETH @alice @bob @charlie
    if content.startswith("/split"):
        parts = content.split()
        if len(parts) < 4:
            return {"error": "Usage: /split amount TOKEN @user1 @user2 ..."}
        amount = parts[1]
        token = parts[2].upper()
        recipients = [p.lstrip("@") for p in parts[3:]]
        return {
            "command": "split",
            "amount": amount,
            "token": token,
            "recipients": recipients,
        }

    return None
