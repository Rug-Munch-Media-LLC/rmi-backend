"""
Wallet clustering engine — identifies related addresses via on-chain behavior.

Part of the wallet domain. Detects wallet clusters using multiple forensic signals:
  - Transaction pattern analysis
  - Temporal proximity detection
  - Common counterparty identification
  - Fund flow tracing
  - Behavioral fingerprinting

Split from monolithic clustering.py into:
  - models.py        Data classes (Transaction, WalletProfile, Cluster, Connection)
  - detection.py     Cluster detection methods (temporal, counterparty, pattern, funding, merge)
  - visualization.py Bubble map + forensic breakdown + cluster report
  - engine.py        WalletClusteringEngine core + global getter + CLI
"""

import logging

from app.domains.wallet.clustering.models import Transaction, WalletProfile, Cluster, Connection
from app.domains.wallet.clustering.engine import WalletClusteringEngine, get_clustering_engine

logger = logging.getLogger(__name__)

__all__ = [
    "Transaction",
    "WalletProfile",
    "Cluster",
    "Connection",
    "WalletClusteringEngine",
    "get_clustering_engine",
]
