"""wallet_manager_v2.py - DEPRECATED shim. Use app.domains.wallet.

Phase 4 of AUDIT-2026-Q3.md moved this to app/domains/wallet/.
Phase 5 (2026-07-23) split the god-file into focused modules.
This shim re-exports the public surface for legacy callers.
"""

# Core manager classes
# Crypto
from app.domains.wallet.crypto import (
    ShamirSecretSharing,
    WalletEncryption,
    ZKAddressVerifier,
)

# Labels
from app.domains.wallet.labels import import_legacy_wallets

# Logger (module-level)
from app.domains.wallet.manager import (
    WalletManagerFacade,
    WalletManagerV2,
    _manager_instance,
    get_wallet_manager_v2,
    logger as _logger,
)

# Data models
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

logger = _logger

__all__ = [
    "CHAIN_REGISTRY",
    "ChainMeta",
    "PaymentRecord",
    "PaymentType",
    "ShamirSecretSharing",
    "WalletEncryption",
    "WalletManagerFacade",
    "WalletManagerV2",
    "WalletPurpose",
    "WalletRecord",
    "WalletRotationSchedule",
    "WalletStatus",
    "WalletTier",
    "ZKAddressVerifier",
    "_build_registry",
    "_manager_instance",
    "get_wallet_manager_v2",
    "import_legacy_wallets",
    "logger",
]
