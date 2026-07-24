"""
Wallet subsystem — enterprise multi-chain wallet management.

Phase 3B of AUDIT-2026-Q3.md refactor.
Split from a 2426-line god file into focused modules on 2026-07-23.

Modules:
  manager.py     — WalletManagerV2 core (< 400 lines)
  models.py      — Enums, dataclasses, chain registry
  crypto.py      — Encryption, Shamir's secret sharing, ZK verification
  db.py          — Persistence operations (load/save vault, keystore)
  analysis.py    — Stats, alerts, sweeping, revenue status
  generation.py  — Multi-chain wallet generation (95+ chains)
  labels.py      — Label/tag management, legacy wallet importing
  entities.py    — Entity resolution, chain lookup, address validation
"""

from __future__ import annotations
from app.domains.wallet.crypto import (
    ShamirSecretSharing,
    WalletEncryption,
    ZKAddressVerifier,
)
from app.domains.wallet.entities import (
    get_chain_meta,
    get_chains_by_group,
    list_chains,
    resolve_chain_address,
)
from app.domains.wallet.labels import import_legacy_wallets
from app.domains.wallet.manager import (
    WalletManagerFacade,
    WalletManagerV2,
    get_wallet_manager_v2,
)
from app.domains.wallet.models import (
    CHAIN_REGISTRY,
    ChainMeta,
    PaymentRecord,
    PaymentType,
    WalletPurpose,
    WalletRecord,
    WalletRotationSchedule,
    WalletStatus,
    WalletTier,
    _build_registry,
)

__all__ = [
    # Manager
    "WalletManagerV2",
    "WalletManagerFacade",
    "get_wallet_manager_v2",
    # Models
    "CHAIN_REGISTRY",
    "ChainMeta",
    "PaymentRecord",
    "PaymentType",
    "WalletPurpose",
    "WalletRecord",
    "WalletRotationSchedule",
    "WalletStatus",
    "WalletTier",
    "_build_registry",
    # Crypto
    "ShamirSecretSharing",
    "WalletEncryption",
    "ZKAddressVerifier",
    # Labels
    "import_legacy_wallets",
    # Entities
    "get_chain_meta",
    "list_chains",
    "get_chains_by_group",
    "resolve_chain_address",
]
