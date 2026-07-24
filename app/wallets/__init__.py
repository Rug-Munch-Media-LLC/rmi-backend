"""
RMI Multi-Chain Wallet Factory - Package
==========================================
Production-grade wallet generation engine supporting 25+ blockchain networks.
Split into:
  - chain_registry:  ChainFamily, ChainConfig, SUPPORTED_CHAINS definitions
  - models:          WalletResult dataclass
  - crypto:          Crypto library imports, AES-256-GCM encryption
  - generation:      Standalone wallet generation functions per chain family
  - factory:         WalletFactory class, singleton, get_wallet_factory
"""

from .chain_registry import SUPPORTED_CHAINS, ChainConfig, ChainFamily
from .crypto import _has_cryptography, decrypt_key, encrypt_key
from .factory import WalletFactory, _factory, get_wallet_factory
from .generation import (
    generate_algorand,
    generate_bitcoin,
    generate_cosmos,
    generate_ed25519,
    generate_evm,
    generate_monero,
    generate_ripple,
    generate_secp256k1_alt,
    generate_solana,
    generate_substrate,
    generate_tezos,
    generate_tron,
)
from .models import WalletResult

__all__ = [
    "ChainConfig",
    "ChainFamily",
    "SUPPORTED_CHAINS",
    "WalletFactory",
    "WalletResult",
    "get_wallet_factory",
]
