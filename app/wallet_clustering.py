"""Backward-compatibility shim — moved to app.domains.wallet.clustering."""

from __future__ import annotations

from app.domains.wallet.clustering import (
    Cluster,
    Connection,
    Transaction,
    WalletClusteringEngine,
    WalletProfile,
    find_related_by_label,
    get_clustering_engine,
    get_wallet_entity,
)


__all__ = [
    "Cluster",
    "Connection",
    "Transaction",
    "WalletClusteringEngine",
    "WalletProfile",
    "find_related_by_label",
    "get_clustering_engine",
    "get_wallet_entity",
]
