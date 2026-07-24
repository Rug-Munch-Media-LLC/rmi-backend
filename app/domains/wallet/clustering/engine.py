"""
WalletClusteringEngine — core class plus global getter and CLI.

Orchestrates all detection methods, manages wallet/transaction/connection state,
and provides the main API for cluster analysis.
"""

import logging
from collections import defaultdict
from datetime import datetime

from app.domains.wallet.clustering.models import (
    Transaction,
    WalletProfile,
    Cluster,
    Connection,
)
from app.domains.wallet.clustering.detection import (
    detect_temporal_clusters,
    detect_common_counterparty_clusters,
    detect_pattern_clusters,
    detect_funding_clusters,
    merge_clusters,
)
from app.domains.wallet.clustering.visualization import (
    get_connections_for_bubble_map,
    generate_bubble_map_data,
    generate_ai_forensic_breakdown,
    get_cluster_report,
)

logger = logging.getLogger(__name__)


class WalletClusteringEngine:
    """
    Advanced wallet clustering engine using multiple forensic signals.
    """

    TEMPORAL_PROXIMITY_MINUTES = 5
    MIN_COMMON_COUNTERPARTIES = 3
    MIN_TRANSACTION_SIMILARITY = 0.7
    MIN_CONNECTION_STRENGTH = 0.3

    def __init__(self) -> None:
        self.wallets: dict[str, WalletProfile] = {}
        self.transactions: list[Transaction] = []
        self.connections: dict[tuple[str, str], Connection] = {}
        self.clusters: dict[str, Cluster] = {}

    def add_transaction(self, tx: Transaction) -> None:
        """Add a transaction to the engine."""
        self.transactions.append(tx)

        if tx.from_address not in self.wallets:
            self.wallets[tx.from_address] = WalletProfile(address=tx.from_address)
        sender = self.wallets[tx.from_address]
        sender.total_transactions += 1
        sender.total_volume += tx.amount
        sender.unique_counterparties.add(tx.to_address)
        sender.transaction_times.append(tx.timestamp)
        sender.programs_used.add(tx.program)

        if tx.to_address not in self.wallets:
            self.wallets[tx.to_address] = WalletProfile(address=tx.to_address)
        receiver = self.wallets[tx.to_address]
        receiver.total_transactions += 1
        receiver.total_volume += tx.amount
        receiver.unique_counterparties.add(tx.from_address)
        receiver.transaction_times.append(tx.timestamp)
        receiver.programs_used.add(tx.program)

        pair = tuple(sorted([tx.from_address, tx.to_address]))
        if pair not in self.connections:
            self.connections[pair] = Connection(
                wallet_a=pair[0], wallet_b=pair[1], strength=0.0, connection_types=[], evidence=[]
            )
        conn = self.connections[pair]
        conn.transaction_count += 1
        conn.total_volume += tx.amount
        conn.evidence.append({
            "signature": tx.signature,
            "timestamp": tx.timestamp.isoformat(),
            "amount": tx.amount,
            "token": tx.token,
        })
        if conn.first_connection is None or tx.timestamp < conn.first_connection:
            conn.first_connection = tx.timestamp
        if conn.last_connection is None or tx.timestamp > conn.last_connection:
            conn.last_connection = tx.timestamp

    def load_from_helius(self, helius_data: list[dict]) -> None:
        """Load transactions from Helius API format."""
        for item in helius_data:
            tx = Transaction(
                signature=item.get("signature", ""),
                timestamp=datetime.fromisoformat(item.get("timestamp", datetime.now().isoformat())),
                from_address=item.get("from", ""),
                to_address=item.get("to", ""),
                amount=item.get("amount", 0.0),
                token=item.get("token", "SOL"),
                program=item.get("program", "unknown"),
                success=item.get("success", True),
            )
            self.add_transaction(tx)
        for wallet in self.wallets.values():
            wallet.calculate_metrics()

    def find_all_clusters(self) -> list[Cluster]:
        """Run all clustering methods and merge results."""
        all_clusters = []
        all_clusters.extend(detect_temporal_clusters(self.wallets, self.transactions, self.TEMPORAL_PROXIMITY_MINUTES))
        all_clusters.extend(detect_common_counterparty_clusters(self.wallets, self.MIN_COMMON_COUNTERPARTIES))
        all_clusters.extend(detect_pattern_clusters(self.wallets, self.MIN_TRANSACTION_SIMILARITY))
        all_clusters.extend(detect_funding_clusters(self.wallets, self.transactions))

        merged = merge_clusters(all_clusters)
        for cluster in merged:
            self.clusters[cluster.cluster_id] = cluster
        return merged

    def get_connections_for_bubble_map(self, center_wallet: str, depth: int = 2,
                                        min_strength: float | None = None, max_wallets: int = 250):
        return get_connections_for_bubble_map(
            self.connections, self.wallets, center_wallet, depth,
            min_strength or self.MIN_CONNECTION_STRENGTH, max_wallets,
        )

    def generate_bubble_map_data(self, center_wallet: str, depth: int = 2, max_wallets: int = 250):
        return generate_bubble_map_data(self.connections, self.wallets, center_wallet, depth, max_wallets)

    def generate_ai_forensic_breakdown(self, center_wallet: str, initial_depth: int = 2,
                                        initial_max_wallets: int = 250, max_expansion_depth: int = 5,
                                        absolute_max_wallets: int = 1000):
        return generate_ai_forensic_breakdown(
            self.connections, self.wallets, self.transactions, center_wallet,
            initial_depth, initial_max_wallets, max_expansion_depth, absolute_max_wallets,
        )

    def get_cluster_report(self, cluster_id: str) -> dict | None:
        cluster = self.clusters.get(cluster_id)
        if not cluster:
            return None
        return get_cluster_report(cluster, self.wallets, self.connections)


# Global engine instance
_clustering_engine = None


def get_clustering_engine() -> WalletClusteringEngine:
    """Get global clustering engine instance."""
    global _clustering_engine
    if _clustering_engine is None:
        _clustering_engine = WalletClusteringEngine()
    return _clustering_engine


if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("WALLET CLUSTERING ENGINE")
    logger.info("=" * 70)

    engine = get_clustering_engine()

    logger.info("\n🔍 Clustering Methods:")
    logger.info("  1. Temporal Proximity - Transactions within 5 minutes")
    logger.info("  2. Common Counterparties - Shared senders/recipients")
    logger.info("  3. Behavioral Patterns - Similar transaction patterns")
    logger.info("  4. Common Funding - Same funding source")

    logger.info("\n📊 Bubble Map Features:")
    logger.info("  - Size = Transaction volume")
    logger.info("  - Color = Wallet type (center/scammer/exchange/unknown)")
    logger.info("  - Line thickness = Connection strength")
    logger.info("  - Interactive depth control")
    logger.info("\n" + "=" * 70)


__all__ = ["WalletClusteringEngine", "get_clustering_engine"]
