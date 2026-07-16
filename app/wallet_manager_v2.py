"""wallet_manager_v2.py - DEPRECATED shim. Use app.domains.wallet.manager.

Phase 4 of AUDIT-2026-Q3.md moved this to app/domains/wallet/manager/.
This shim re-exports the public surface for legacy callers.
"""

from app.domains.wallet.manager import *  # noqa: F403
from app.domains.wallet.manager import (  # noqa: F401
    CHAIN_REGISTRY,
    ChainMeta,
    PaymentRecord,
    PaymentType,
    ShamirSecretSharing,
    WalletEncryption,
    WalletManagerFacade,
    WalletManagerV2,
    WalletPurpose,
    WalletRecord,
    WalletRotationSchedule,
    WalletStatus,
    WalletTier,
    ZKAddressVerifier,
    _build_registry,
    _wallet_manager_instance,
    get_wallet_manager_v2,
    import_legacy_wallets,
    logger,
)
