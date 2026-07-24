"""
RMI Multi-Chain Wallet Factory - Re-export Shim
==================================================
Backward-compatible re-export. All logic moved to app/wallets/ package.

Usage (unchanged):
    from app.wallet_factory import WalletFactory, get_wallet_factory, WalletResult
"""


from __future__ import annotations

from app.wallets.chain_registry import SUPPORTED_CHAINS, ChainConfig, ChainFamily
from app.wallets.crypto import _has_cryptography, decrypt_key, encrypt_key
from app.wallets.factory import WalletFactory, get_wallet_factory
from app.wallets.models import WalletResult

__all__ = [
    "SUPPORTED_CHAINS",
    "ChainConfig",
    "ChainFamily",
    "WalletFactory",
    "WalletResult",
    "_has_cryptography",
    "decrypt_key",
    "encrypt_key",
    "get_wallet_factory",
]
