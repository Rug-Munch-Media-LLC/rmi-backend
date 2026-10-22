"""x402 exact — request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

ALLOWED_CHAINS = frozenset(
    {"solana", "ethereum", "base", "bsc", "arbitrum", "polygon", "optimism", "avalanche"}
)


def _validate_chain(value: str) -> str:
    """Reject chains we do not index."""
    lowered = value.strip().lower()
    if lowered not in ALLOWED_CHAINS:
        raise ValueError(f"unsupported chain: {value}")
    return lowered


def _validate_address(value: str) -> str:
    """Reject obviously malformed addresses before any provider call."""
    cleaned = value.strip()
    if not cleaned.replace("0x", "").replace("0X", "").isalnum():
        raise ValueError("address must be alphanumeric")
    return cleaned


class ScanRequest(BaseModel):
    """Token scan / security snapshot request."""

    chain: str = Field(default="solana", max_length=32)
    address: str = Field(min_length=8, max_length=128)

    _chain = field_validator("chain")(_validate_chain)
    _address = field_validator("address")(_validate_address)


class WalletRequest(BaseModel):
    """Wallet security intel request."""

    chain: str = Field(default="ethereum", max_length=32)
    address: str = Field(min_length=8, max_length=128)

    _chain = field_validator("chain")(_validate_chain)
    _address = field_validator("address")(_validate_address)
