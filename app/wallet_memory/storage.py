"""
Wallet Memory Storage - ClickHouse + Redis unified interface.
=============================================================
THIN ADAPTER. Canonical source: `rugmunch_scanner.wallet_memory.storage`.
The implementation was superseded by the private rugmunch-scanner repo.
"""

from rugmunch_scanner.wallet_memory.storage import (
    WalletStorage,
    ensure_schema,
    get_storage,
)

__all__ = [
    "WalletStorage",
    "ensure_schema",
    "get_storage",
]
