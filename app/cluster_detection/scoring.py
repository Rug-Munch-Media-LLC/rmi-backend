"""
Cluster Detection Pro - Scoring Utilities
==========================================
Standalone scoring functions for cluster confidence, risk, behavioral
similarity, and cluster classification.

Extracted from ClusterDetectionPro methods to reduce module size.
"""

from __future__ import annotations
from collections import defaultdict
from typing import Any

from app.cluster_detection.models import ClusterType, WalletCluster

# ── Signal weights for confidence calculation ────────────────────

SIGNAL_WEIGHTS: dict[str, float] = {
    "temporal_proximity": 0.20,
    "common_counterparties": 0.15,
    "behavioral_similarity": 0.20,
    "common_funding": 0.15,
    "transaction_patterns": 0.15,
    "code_similarity": 0.10,
    "social_connections": 0.05,
}

def calculate_cluster_confidence(cluster: WalletCluster) -> float:
    """Calculate overall confidence score for a cluster."""
    if not cluster.detection_signals:
        return 0.0

    total_weight = 0.0
    weighted_score = 0.0

    for signal in cluster.detection_signals:
        weight = SIGNAL_WEIGHTS.get(signal.signal_type, 0.1)
        weighted_score += signal.strength * weight
        total_weight += weight

    # Boost for multiple signals
    signal_count = len(cluster.detection_signals)
    boost = min(0.2, signal_count * 0.05)

    confidence = (weighted_score / total_weight) + boost if total_weight > 0 else 0.0
    return min(1.0, confidence)

def classify_cluster_type(cluster: WalletCluster) -> ClusterType:
    """Classify the type of cluster based on signals."""
    signal_types = [s.signal_type for s in cluster.detection_signals]

    if "sleeper_pattern" in signal_types:
        return ClusterType.SLEEPER

    if "common_funding" in signal_types:
        return ClusterType.FUNDING

    if "temporal_proximity" in signal_types and len(cluster.wallets) > 10:
        return ClusterType.BOTNET

    if "behavioral_similarity" in signal_types:
        return ClusterType.SYBIL

    return ClusterType.UNKNOWN

def calculate_risk_score(cluster: WalletCluster) -> float:
    """Calculate risk score for a cluster."""
    score = 0.0

    # Botnet = high risk
    if cluster.cluster_type == ClusterType.BOTNET:
        score += 40

    # Sleeper = suspicious
    if cluster.cluster_type == ClusterType.SLEEPER:
        score += 30

    # Large clusters = higher risk
    score += min(20, len(cluster.wallets) * 0.5)

    # High confidence = more reliable risk assessment
    score *= 0.5 + cluster.confidence * 0.5

    return min(100, score)

def calculate_behavioral_similarity(fp1: dict, fp2: dict) -> float:
    """Calculate similarity between two behavioral fingerprints."""
    scores = []

    # Transaction count similarity
    if fp1["tx_count"] > 0 and fp2["tx_count"] > 0:
        ratio = min(fp1["tx_count"], fp2["tx_count"]) / max(fp1["tx_count"], fp2["tx_count"])
        scores.append(ratio)

    # Average amount similarity
    if fp1["avg_amount"] > 0 and fp2["avg_amount"] > 0:
        ratio = min(fp1["avg_amount"], fp2["avg_amount"]) / max(
            fp1["avg_amount"], fp2["avg_amount"]
        )
        scores.append(ratio)

    # Hour distribution similarity (cosine similarity)
    if fp1["hour_distribution"] and fp2["hour_distribution"]:
        dot = sum(
            a * b
            for a, b in zip(fp1["hour_distribution"], fp2["hour_distribution"], strict=False)
        )
        norm1 = sum(a**2 for a in fp1["hour_distribution"]) ** 0.5
        norm2 = sum(a**2 for a in fp2["hour_distribution"]) ** 0.5
        if norm1 > 0 and norm2 > 0:
            scores.append(dot / (norm1 * norm2))

    return sum(scores) / len(scores) if scores else 0.0

def distribution(values: list[int], bins: int = 24) -> list[float]:
    """Calculate distribution of values."""
    counts = [0] * bins
    for v in values:
        if 0 <= v < bins:
            counts[v] += 1
    total = sum(counts)
    return [c / total if total > 0 else 0 for c in counts]

def merge_clusters_by_overlap(
    all_signals: dict, wallets: list[str]
) -> list[WalletCluster]:
    """Merge clusters that share wallets."""
    wallet_clusters = defaultdict(set)

    for wallet in wallets:
        for _method, clusters in all_signals[wallet].items():
            for cluster in clusters:
                wallet_clusters[wallet].add(id(cluster))

    # Find connected components (wallets that appear in same clusters)
    visited = set()
    merged = []

    for wallet in wallets:
        if wallet in visited:
            continue

        # Find all connected wallets
        component = set()
        queue = [wallet]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            component.add(current)

            # Add wallets that share clusters
            for cluster_id in wallet_clusters[current]:
                for w, c_ids in wallet_clusters.items():
                    if cluster_id in c_ids and w not in visited:
                        queue.append(w)

        if len(component) >= 2:
            # Collect all signals for this component
            all_component_signals = []
            for w in component:
                for _method, clusters in all_signals[w].items():
                    for cluster in clusters:
                        all_component_signals.extend(cluster.detection_signals)

            merged_cluster = WalletCluster(
                cluster_id=f"merged_{len(merged)}",
                cluster_type=ClusterType.UNKNOWN,
                confidence=0.0,
                wallets=component,
                detection_signals=all_component_signals,
                detection_method="merged",
            )
            merged.append(merged_cluster)

    return merged
