"""
RMI Wallet Manager v2 - Core wallet operations.

Split from 2,426-line god file on 2026-07-23.
This module contains only the core WalletManagerV2 class and facade.
All other functionality has been moved to focused modules:
  - models.py     — data models and chain registry
  - crypto.py     — encryption and security
  - db.py         — persistence operations
  - analysis.py   — stats, alerts, sweeping
  - generation.py — multi-chain wallet generation
  - labels.py     — label management
  - entities.py   — entity resolution
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.domains.wallet.analysis import WalletAnalysisMixin
from app.domains.wallet.db import WalletDBMixin
from app.domains.wallet.generation import WalletGenerationMixin

if TYPE_CHECKING:
    from app.domains.wallet.models import PaymentRecord, WalletRecord

logger = get_logger(__name__)


class WalletManagerV2(WalletDBMixin, WalletAnalysisMixin, WalletGenerationMixin):
    """Enterprise multi-chain wallet manager with HD support."""

    def __init__(self, vault_path: str | None = None, password: str | None = None):
        vault_dir = vault_path or os.path.join(os.getcwd(), "data", "wallets")
        self.VAULT_PATH = os.path.join(vault_dir, "vault.json")
        self.PAYMENTS_PATH = os.path.join(vault_dir, "payments.jsonl")
        self.KEYSTORE_PATH = os.path.join(vault_dir, "keystore.json")
        self.SHARES_PATH = os.path.join(vault_dir, "shares")
        self.encryption_password = password or ""
        self._wallets: dict[str, WalletRecord] = {}
        self._payments: list[PaymentRecord] = []
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the wallet manager."""
        self._load_vault()
        self._initialized = True

    async def close(self) -> None:
        """Close the wallet manager."""
        self._save_vault()
        self._initialized = False


class WalletManagerFacade:
    """Facade for backward compatibility with v1 API."""

    def __init__(self, manager: WalletManagerV2):
        self._manager = manager

    def __getattr__(self, name: str) -> Any:
        return getattr(self._manager, name)


# Singleton instance
_manager_instance: WalletManagerV2 | None = None


def get_wallet_manager_v2(vault_path: str | None = None, password: str | None = None) -> WalletManagerV2:
    """Get or create the singleton wallet manager instance."""
    global _manager_instance
    if _manager_instance is None:
        _manager_instance = WalletManagerV2(vault_path=vault_path, password=password)
    return _manager_instance
