"""
from typing import Any
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
  - neo4j_labels.py  Neo4j entity resolution from wallet_labels graph
"""

import logging

from app.domains.wallet.clustering.engine import WalletClusteringEngine, get_clustering_engine
from app.domains.wallet.clustering.models import Cluster, Connection, Transaction, WalletProfile

logger = logging.getLogger(__name__)


async def get_wallet_entity(address: str) -> dict[str, Any] | None:
    """Get entity info for a wallet address from Neo4j label graph.

    Called by rugmaps_engine graph.py for wallet entity enrichment.
    Falls back gracefully if Neo4j is unavailable.
    """
    try:
        from app.domains.wallet.neo4j_client import query_wallet_entity

        return await query_wallet_entity(address)
    except ImportError:
        logger.debug("neo4j_client not available")
    except Exception as e:
        logger.debug("Neo4j entity query failed for %s: %s", address[:12], e)
    return None


async def find_related_by_label(address: str) -> list[dict[str, Any]]:
    """Find wallets sharing labels with this address (entity resolution)."""
    try:
        from app.domains.wallet.neo4j_client import find_related_wallets

        return await find_related_wallets(address)
    except ImportError:
        logger.debug("neo4j_client not available")
    except Exception as e:
        logger.debug("Neo4j related wallets query failed: %s", e)
    return []


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
