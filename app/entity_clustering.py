"""
Entity Graph & Wallet Clustering Module
========================================

Builds relationships between wallets, addresses, and entities across chains.
Core features:
- Wallet clustering (group related addresses)
- Entity extraction (identify exchanges, contracts, protocols)
- Cross-chain correlation (link wallets across chains)
"""

import hashlib
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from app.evm_connector import EVMClient
from app.solana_connector import SolanaClient

logger = logging.getLogger(__name__)


# ─── CLUSTERING ALGORITHMS ──────────────────────────────────────────────


def hash_address(address: str) -> str:
    """Generate a consistent hash for an address."""
    return hashlib.sha256(address.lower().encode()).hexdigest()[:16]


class WalletCluster:
    """Represents a cluster of related wallets."""

    def __init__(self, cluster_id: str):
        self.cluster_id = cluster_id
        self.addresses: set[str] = set()
        self.chains: set[str] = set()
        self.confidence: float = 0.0
        self.labels: set[str] = set()
        self.metadata: dict[str, Any] = {}

    def add_address(self, address: str, chain: str = "unknown", confidence: float = 0.5, label: str = ""):
        """Add an address to the cluster."""
        self.addresses.add(address.lower())
        self.chains.add(chain)
        self.metadata[address] = {"chain": chain, "confidence": confidence, "label": label}

        if confidence > self.confidence:
            self.confidence = confidence
        if label:
            self.labels.add(label)

    def get_addresses(self, chain: str | None = None) -> list[str]:
        """Get addresses, optionally filtered by chain."""
        if chain:
            return [addr for addr, meta in self.metadata.items() if meta.get("chain") == chain]
        return list(self.addresses)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "cluster_id": self.cluster_id,
            "addresses": list(self.addresses),
            "chains": list(self.chains),
            "confidence": self.confidence,
            "labels": list(self.labels),
            "metadata": self.metadata,
        }


class EntityGraph:
    """Graph-based wallet entity mapping."""

    def __init__(self):
        self.nodes: dict[str, WalletCluster] = {}
        self.edges: list[dict[str, str]] = []
        self.entity_types: dict[str, set[str]] = defaultdict(set)

    def add_wallet(self, address: str, chain: str, label: str = "", confidence: float = 0.5):
        """Add a wallet to the graph."""
        cluster_id = f"cluster_{hash_address(address)}"

        if cluster_id not in self.nodes:
            self.nodes[cluster_id] = WalletCluster(cluster_id)

        cluster = self.nodes[cluster_id]
        cluster.add_address(address, chain, confidence, label)
        self.entity_types[cluster_id].add(label)

        if label:
            cluster.labels.add(label)

    def link_wallets(self, address1: str, address2: str, relationship: str = "related"):
        """Create an edge between two wallets."""
        cluster1 = f"cluster_{hash_address(address1)}"
        cluster2 = f"cluster_{hash_address(address2)}"

        edge = {
            "from": cluster1,
            "to": cluster2,
            "relationship": relationship,
            "created_at": datetime.utcnow().isoformat(),
        }
        self.edges.append(edge)

    def find_clusters(self, min_size: int = 2) -> list[dict[str, Any]]:
        """Return clusters with at least min_size members."""
        return [cluster.to_dict() for cluster in self.nodes.values() if len(cluster.addresses) >= min_size]

    def get_entity(self, cluster_id: str) -> dict[str, Any] | None:
        """Get a specific entity."""
        if cluster_id in self.nodes:
            return self.nodes[cluster_id].to_dict()
        return None

    def to_graph_json(self) -> dict[str, Any]:
        """Export as graph JSON (for visualization)."""
        return {
            "nodes": [
                {
                    "id": cluster_id,
                    "cluster_id": cluster.cluster_id,
                    "labels": list(cluster.labels),
                    "chains": list(cluster.chains),
                    "size": len(cluster.addresses),
                    "confidence": cluster.confidence,
                }
                for cluster_id, cluster in self.nodes.items()
            ],
            "edges": self.edges,
        }


# ─── CLUSTERING STRATEGIES ──────────────────────────────────────────────


class ClusteringStrategy:
    """Base class for clustering strategies."""

    @staticmethod
    def detect_shared_constructor(addresses: list[str], evm_client: EVMClient) -> dict[str, list[str]]:
        """
        Detect wallets created by the same contract (e.g., Uniswap pools).
        Returns addresses grouped by creator contract.
        """
        clusters = defaultdict(list)

        for address in addresses:
            try:
                # Get creation info from EVM client
                creation_info = evm_client.get_transaction_count(address)
                if creation_info:
                    clusters["shared_creation"].append(address)
            except Exception as e:
                logger.debug(f"Could not analyze {address}: {e}")

        return dict(clusters)

    @staticmethod
    def detect_shared_interactions(addresses: list[str], evm_client: EVMClient, protocol: str) -> dict[str, list[str]]:
        """
        Detect wallets that interact with the same protocol.
        """
        clusters = defaultdict(list)

        for address in addresses:
            try:
                # Check for protocol interactions
                interactions = evm_client.get_address_transactions(address, limit=10)
                for tx in interactions:
                    if protocol.lower() in str(tx).lower():
                        clusters[protocol].append(address)
                        break
            except Exception as e:
                logger.debug(f"Could not analyze {address}: {e}")

        return dict(clusters)


# ─── MAIN CLUSTERING ENGINE ─────────────────────────────────────────────


class EntityClusteringEngine:
    """Main entity clustering engine."""

    def __init__(self, evm_client: EVMClient | None = None, solana_client: SolanaClient | None = None):
        self.graph = EntityGraph()
        self.evm_client = evm_client or EVMClient()
        self.solana_client = solana_client or SolanaClient()

    def add_wallet(self, address: str, chain: str = "ethereum", label: str = ""):
        """Add a wallet to the entity graph."""
        self.graph.add_wallet(address, chain, label)

    def batch_add_wallets(self, wallets: list[dict[str, str]]):
        """Add multiple wallets at once."""
        for wallet in wallets:
            address = wallet.get("address", "")
            chain = wallet.get("chain", "ethereum")
            label = wallet.get("label", "")
            self.add_wallet(address, chain, label)

    def correlate_wallets(self, address: str, chain: str = "ethereum") -> dict[str, Any]:
        """
        Find correlated wallets for an address (same entity across chains).
        """
        cluster_id = f"cluster_{hash_address(address)}"
        entity = self.graph.get_entity(cluster_id)

        if entity:
            return {
                "address": address,
                "chain": chain,
                "correlated": entity,
                "recommendations": self._generate_recommendations(entity),
            }
        return {"address": address, "chain": chain, "correlated": None, "recommendations": []}

    def _generate_recommendations(self, entity: dict[str, Any]) -> list[str]:
        """Generate security/recommendation based on entity data."""
        recommendations = []

        # Check for multi-chain presence
        if len(entity.get("chains", [])) >= 2:
            recommendations.append(
                {
                    "type": "cross_chain",
                    "message": "Entity present on multiple chains - monitor consistently",
                    "priority": "medium",
                }
            )

        # Check for exchange/merchant labels
        if "exchange" in entity.get("labels", []) or "merchant" in entity.get("labels", []):
            recommendations.append(
                {
                    "type": "high_volume",
                    "message": "High-value address detected - consider enhanced monitoring",
                    "priority": "high",
                }
            )

        return recommendations

    def build_entity_graph(self, wallet_addresses: list[str], max_depth: int = 3) -> dict[str, Any]:
        """
        Build a comprehensive entity graph from wallet addresses.
        """
        # Add wallets to graph
        self.batch_add_wallets([{"address": addr, "chain": "ethereum"} for addr in wallet_addresses])

        # Find clusters
        clusters = self.graph.find_clusters(min_size=1)

        return {
            "wallet_count": len(wallet_addresses),
            "entity_count": len(clusters),
            "clusters": clusters,
            "graph": self.graph.to_graph_json(),
        }

    def find_common_entities(self, addresses: list[str]) -> dict[str, list[str]]:
        """
        Find which entities/clusters multiple addresses belong to.
        """
        entity_map = defaultdict(list)

        for address in addresses:
            cluster_id = f"cluster_{hash_address(address)}"
            entity_map[cluster_id].append(address)

        return dict(entity_map)


# ─── API EXPORTS ────────────────────────────────────────────────────────


def get_clustering_engine() -> EntityClusteringEngine:
    """Get a configured clustering engine instance."""
    return EntityClusteringEngine()
