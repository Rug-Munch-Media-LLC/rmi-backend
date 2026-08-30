"""
Wallet Memory Bank - Persistent wallet intelligence across chains.
==================================================================
Consolidates wallet clustering, labeling, entity resolution, and risk scoring
into a single coherent system that feeds SENTINEL token scans and powers
standalone wallet exploration.

Two products, one backbone:
  - SENTINEL (token scanner) consumes deployer intelligence from here
  - WalletSafe (wallet explorer) surfaces entity graphs and risk profiles

This module is now a THIN ADAPTER — the canonical implementation lives in
`rugmunch_scanner.wallet_memory`. It re-exports the public surface so the
FastAPI import path (`app.wallet_memory.*`) stays stable.
"""

from .engine import WalletMemoryEngine, get_wallet_engine
from .ingestion import WalletIngestionPipeline, get_ingestion_pipeline

__all__ = [
    "WalletIngestionPipeline",
    "WalletMemoryEngine",
    "get_ingestion_pipeline",
    "get_wallet_engine",
]
