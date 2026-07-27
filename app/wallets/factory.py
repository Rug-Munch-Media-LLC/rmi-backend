"""
Wallet Factory - Core Factory Class
=====================================
WalletFactory class with generate, batch, rotation, storage, and singleton.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.wallets.chain_registry import SUPPORTED_CHAINS
from app.wallets.crypto import encrypt_key
from app.wallets.generation import GENERATORS

if TYPE_CHECKING:
    from app.wallets.models import WalletResult

logger = get_logger("wallet_factory")

class WalletFactory:
    """
    Multi-chain wallet generation engine.

    Generates cryptographically secure wallets for 25+ blockchain networks.
    Supports encrypted key storage with AES-256-GCM + Argon2.
    """

    def __init__(self, encrypt_keys: bool = True, master_password: str | None = None) -> None:
        self._encrypt = encrypt_keys
        self._password = master_password or os.getenv("RMI_WALLET_MASTER_PASSWORD", "")
        self._key_store: dict[str, WalletResult] = {}
        self._rotation_index: dict[str, int] = {}
        self._generation_count: int = 0

    # ── Generation ──────────────────────────────────────────────

    def generate(
        self, chain_key: str, label: str = "", tags: list[str] | None = None
    ) -> WalletResult:
        """Generate a wallet for any supported chain."""
        cfg = SUPPORTED_CHAINS.get(chain_key.lower())
        if not cfg:
            raise ValueError(
                f"Unsupported chain: {chain_key}. Supported: {list(SUPPORTED_CHAINS.keys())}"
            )

        now = datetime.now(UTC).isoformat()

        gen_fn = GENERATORS.get(cfg.family)
        if gen_fn is None:
            raise ValueError(f"Generation not implemented for {cfg.family}")

        result = gen_fn(cfg)

        result.chain = chain_key
        result.chain_name = cfg.name
        result.symbol = cfg.symbol
        result.created_at = now
        result.label = label
        result.tags = tags or []

        if self._encrypt and self._password:
            result.private_key_hex = encrypt_key(result.private_key_hex, self._password)
            result.encrypted = True

        self._generation_count += 1
        return result

    def generate_batch(self, chains: list[str], label_prefix: str = "") -> dict[str, WalletResult]:
        """Generate wallets for multiple chains at once."""
        results = {}
        for i, chain in enumerate(chains):
            label = f"{label_prefix}_{i + 1}" if label_prefix else ""
            results[chain] = self.generate(chain, label=label)
        return results

    def generate_all(self) -> dict[str, WalletResult]:
        """Generate wallets for ALL supported chains."""
        return self.generate_batch(list(SUPPORTED_CHAINS.keys()), label_prefix="full_suite")

    # ── Storage ─────────────────────────────────────────────────

    def save_to_vault(
        self, wallet: WalletResult, vault_path: str = os.getenv("WALLET_VAULT_PATH", "/path/to/wallets/vault.json")
    ) -> None:
        """Save wallet to encrypted vault."""
        os.makedirs(os.path.dirname(vault_path), exist_ok=True, mode=0o700)

        vault = {}
        if os.path.exists(vault_path):
            with open(vault_path) as f:
                vault = json.load(f)

        key = f"{wallet.chain}:{wallet.address[:12]}"
        vault[key] = wallet.to_full_dict()

        # Count wallet entries (exclude _meta)
        wallet_count = sum(1 for k in vault if not k.startswith("_"))
        vault["_meta"] = {
            "updated": datetime.now(UTC).isoformat(),
            "total_wallets": wallet_count,
            "encrypted": self._encrypt,
        }

        with open(vault_path, "w") as f:
            json.dump(vault, f, indent=2)
        os.chmod(vault_path, 0o600)

        logger.info(f"Wallet {key} saved to vault ({vault_path})")

    # ── Rotation ────────────────────────────────────────────────

    def rotate(
        self, chain_key: str, vault_path: str = os.getenv("WALLET_VAULT_PATH", "/path/to/wallets/vault.json")
    ) -> WalletResult:
        """Generate a new wallet and mark it as the active rotation for this chain."""
        wallet = self.generate(
            chain_key, label=f"rotation_{int(time.time())}", tags=["rotated", "active"]
        )
        self.save_to_vault(wallet, vault_path)

        idx = self._rotation_index.get(chain_key, 0)
        self._rotation_index[chain_key] = idx + 1

        logger.info(f"Rotated {chain_key} wallet -> {wallet.address[:16]}... (rotation #{idx + 1})")
        return wallet

    # ── Decryption ──────────────────────────────────────────────

    def decrypt_key(self, encrypted: str) -> str:
        """Decrypt an AES-256-GCM encrypted private key."""
        from app.wallets.crypto import decrypt_key as _decrypt

        return _decrypt(encrypted, self._password)

    # ── Stats ───────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "generation_count": self._generation_count,
            "chains_supported": len(SUPPORTED_CHAINS),
            "chain_families": len({c.family for c in SUPPORTED_CHAINS.values()}),
            "rotations": dict(self._rotation_index),
            "encryption_enabled": self._encrypt,
            "chains": {
                k: {"name": c.name, "symbol": c.symbol, "family": c.family}
                for k, c in SUPPORTED_CHAINS.items()
            },
        }

# ── Singleton ───────────────────────────────────────────────────

_factory: WalletFactory | None = None

def get_wallet_factory(encrypt: bool = True) -> WalletFactory:
    global _factory
    if _factory is None:
        _factory = WalletFactory(encrypt_keys=encrypt)
    return _factory
