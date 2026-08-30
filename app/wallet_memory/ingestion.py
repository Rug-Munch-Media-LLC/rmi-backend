"""
Wallet Ingestion Pipeline - Auto-pull data from Helius (Solana) & Etherscan (EVM).
=================================================================================
THIN ADAPTER. Canonical source: `rugmunch_scanner.wallet_memory.ingestion`.
The implementation was superseded by the private rugmunch-scanner repo.
"""

from rugmunch_scanner.wallet_memory.ingestion import (
    WalletIngestionPipeline,
    get_ingestion_pipeline,
)

__all__ = [
    "WalletIngestionPipeline",
    "get_ingestion_pipeline",
]
