"""
Cluster Detection Pro - Engine
===============================
Orchestrator that runs all detection methods, merges results,
and provides the public API. Singleton management.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from app.cluster_detection.detectors import (
    detect_behavioral_clusters,
    detect_counterparty_clusters,
    detect_funding_clusters,
    detect_temporal_clusters,
)
from app.cluster_detection.detectors_advanced import (
    detect_ml_clusters,
    detect_pattern_clusters,
    detect_sleeper_clusters,
)
from app.cluster_detection.models import FundingPath, WalletCluster
from app.cluster_detection.scoring import (
    calculate_cluster_confidence,
    calculate_risk_score,
    classify_cluster_type,
    merge_clusters_by_overlap,
)

logger = logging.getLogger(__name__)


class ClusterDetectionPro:
    """
    Professional-grade cluster detection using multiple signals.

    Runs 7 detection methods, merges overlapping clusters, and
    produces confidence-scored results.
    """

    def __init__(self) -> None:
        self.wallet_profiles: dict[str, dict] = {}
        self.transactions: list[dict] = []
        self.clusters: dict[str, WalletCluster] = {}
        self.funding_paths: dict[tuple[str, str], FundingPath] = {}

    async def detect_clusters(
        self, wallets: list[str], min_confidence: float = 0.4, include_sleepers: bool = True
    ) -> list[WalletCluster]:
        """
        Detect clusters among a set of wallets.

        Args:
            wallets: List of wallet addresses to analyze
            min_confidence: Minimum confidence threshold
            include_sleepers: Whether to detect sleeper clusters

        Returns:
            List of detected clusters
        """
        detected_clusters = []

        # Load wallet profiles
        await self._load_wallet_profiles(wallets)

        # Run all detection methods
        detection_methods = [
            ("temporal_proximity", detect_temporal_clusters),
            ("common_counterparties", detect_counterparty_clusters),
            ("behavioral_similarity", detect_behavioral_clusters),
            ("common_funding", detect_funding_clusters),
            ("transaction_patterns", detect_pattern_clusters),
            ("machine_learning", detect_ml_clusters),
        ]

        if include_sleepers:
            detection_methods.append(("sleeper", detect_sleeper_clusters))

        all_signals: dict[str, dict[str, list[WalletCluster]]] = defaultdict(lambda: defaultdict(list))

        for method_name, method_func in detection_methods:
            clusters = await method_func(self.wallet_profiles, wallets)
            for cluster in clusters:
                for wallet in cluster.wallets:
                    all_signals[wallet][method_name].append(cluster)

        # Merge overlapping clusters
        merged_clusters = merge_clusters_by_overlap(all_signals, wallets)

        # Calculate confidence and filter
        for cluster in merged_clusters:
            cluster.confidence = calculate_cluster_confidence(cluster)
            if cluster.confidence >= min_confidence:
                cluster.cluster_type = classify_cluster_type(cluster)
                cluster.risk_score = calculate_risk_score(cluster)
                detected_clusters.append(cluster)

        # Store clusters
        for cluster in detected_clusters:
            self.clusters[cluster.cluster_id] = cluster

        return sorted(detected_clusters, key=lambda c: c.confidence, reverse=True)

    async def _load_wallet_profiles(self, wallets: list[str]) -> None:
        """Load profiles for all wallets."""
        for wallet in wallets:
            if wallet not in self.wallet_profiles:
                self.wallet_profiles[wallet] = await self._fetch_wallet_profile(wallet)

    async def _fetch_wallet_profile(self, wallet: str) -> dict:
        """Fetch profile for a single wallet."""
        # In production, query Helius/Arkham
        return {
            "address": wallet,
            "transactions": [],
            "first_seen": datetime.now() - timedelta(days=30),
            "last_seen": datetime.now(),
            "total_volume": 10000.0,
            "transaction_count": 100,
            "unique_counterparties": set(),
            "token_holdings": {},
            "programs_used": set(),
        }

    async def trace_funding_path(self, source: str, target: str, max_depth: int = 5) -> FundingPath | None:
        """Trace funding path between two wallets."""
        # BFS to find path
        visited = {source}
        queue = [(source, [source])]

        while queue and len(queue[0][1]) <= max_depth:
            current, path = queue.pop(0)

            if current == target:
                return FundingPath(
                    source=source,
                    target=target,
                    path=path,
                    total_amount=0.0,
                    transaction_count=len(path) - 1,
                    first_funding=datetime.now(),
                    last_funding=datetime.now(),
                )

            # Get outgoing transactions
            profile = self.wallet_profiles.get(current, {})
            for tx in profile.get("transactions", []):
                if tx.get("from") == current:
                    next_wallet = tx.get("to")
                    if next_wallet and next_wallet not in visited:
                        visited.add(next_wallet)
                        queue.append((next_wallet, [*path, next_wallet]))

        return None


# Global instance
_cluster_pro = None


def get_cluster_detection_pro() -> ClusterDetectionPro:
    """Get global ClusterDetectionPro instance."""
    global _cluster_pro
    if _cluster_pro is None:
        _cluster_pro = ClusterDetectionPro()
    return _cluster_pro


# Alias for backward compatibility with code importing get_cluster_detector
get_cluster_detector = get_cluster_detection_pro
