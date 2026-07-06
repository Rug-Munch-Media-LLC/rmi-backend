"""Backward-compat shim — moved to app.wallet.manager in P3B."""
from app.wallet.manager import *  # noqa: F401,F403
from app.wallet.manager import (  # noqa: F401
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
    _build_registry,
)
