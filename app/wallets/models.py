"""
Wallet Factory - Wallet Result Model
======================================
WalletResult dataclass with safe/full dict serialization.
"""

from __future__ import annotations
from typing import Any

from dataclasses import asdict, dataclass, field


@dataclass
class WalletResult:
    """Generated wallet with metadata."""

    chain: str
    chain_name: str
    symbol: str
    address: str
    private_key_hex: str
    public_key_hex: str = ""
    mnemonic: str = ""
    derivation_path: str = ""
    address_type: str = ""
    created_at: str = ""
    tags: list[str] = field(default_factory=list)
    label: str = ""
    encrypted: bool = False

    def to_safe_dict(self) -> dict:
        """Return dict WITHOUT private key (for API responses)."""
        d = asdict(self)
        d.pop("private_key_hex", None)
        d.pop("mnemonic", None)
        d["has_private_key"] = True
        return d

    def to_full_dict(self) -> dict:
        """Return full dict WITH private key (only for secure operations)."""
        return asdict(self)
