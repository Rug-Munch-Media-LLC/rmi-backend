"""
Wallet Memory Engine - Orchestrator for the Wallet Memory Bank.
================================================================
THIN ADAPTER. Canonical source: `rugmunch_scanner.wallet_memory.engine`.
The implementation was superseded by the private rugmunch-scanner repo.
"""

from rugmunch_scanner.wallet_memory.engine import (
    WalletMemoryEngine,
    get_wallet_engine,
)

__all__ = [
    "WalletMemoryEngine",
    "get_wallet_engine",
]
