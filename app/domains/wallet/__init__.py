"""Wallet subsystem.

Phase 3B of AUDIT-2026-Q3.md.

Re-exports the canonical public API of WalletManagerV2. The bulk of the
implementation lives in app.wallet.manager (moved verbatim from
app.wallet_manager_v2 on 2026-07-07).
"""
from app.domains.wallet.manager import (  # noqa: F401
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
    get_wallet_manager_v2,
    import_legacy_wallets,
)
