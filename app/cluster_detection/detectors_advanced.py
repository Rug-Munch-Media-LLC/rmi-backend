"""
Cluster Detection Pro - Advanced Detection Methods
===================================================
ML-based and sleeper detection methods.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sklearn.cluster import DBSCAN
from sklearn.preprocessing import StandardScaler

from app.cluster_detection.models import ClusterSignal, ClusterType, WalletCluster


async def detect_pattern_clusters(
    wallet_profiles: dict[str, dict],
    wallets: list[str],
) -> list[WalletCluster]:
    """Detect clusters based on transaction patterns (stub)."""
    _ = wallet_profiles, wallets
    return []

async def detect_ml_clusters(
    wallet_profiles: dict[str, dict],
    wallets: list[str],
) -> list[WalletCluster]:
    """Detect clusters using machine learning (DBSCAN)."""
    features = []
    wallet_list = []

    for wallet in wallets:
        profile = wallet_profiles.get(wallet, {})
        if profile.get("transaction_count", 0) < 5:
            continue

        feature_vector = [
            profile.get("transaction_count", 0),
            profile.get("total_volume", 0),
            len(profile.get("unique_counterparties", set())),
            len(profile.get("programs_used", set())),
        ]
        features.append(feature_vector)
        wallet_list.append(wallet)

    if len(features) < 3:
        return []

    scaler = StandardScaler()
    features_scaled = scaler.fit_transform(features)

    clustering = DBSCAN(eps=0.5, min_samples=2).fit(features_scaled)
    labels = clustering.labels_

    clusters_dict = defaultdict(list)
    for wallet, label in zip(wallet_list, labels, strict=False):
        if label != -1:
            clusters_dict[label].append(wallet)

    clusters = []
    for label, cluster_wallets in clusters_dict.items():
        if len(cluster_wallets) >= 2:
            cluster = WalletCluster(
                cluster_id=f"ml_{len(clusters)}",
                cluster_type=ClusterType.UNKNOWN,
                confidence=0.6,
                wallets=set(cluster_wallets),
                detection_signals=[
                    ClusterSignal(
                        signal_type="machine_learning",
                        strength=0.6,
                        evidence={"algorithm": "DBSCAN"},
                        description="ML-detected behavioral similarity",
                    )
                ],
                detection_method="machine_learning",
            )
            clusters.append(cluster)

    return clusters

async def detect_sleeper_clusters(
    wallet_profiles: dict[str, dict],
    wallets: list[str],
) -> list[WalletCluster]:
    """Detect sleeper clusters - dormant wallets waiting to activate."""
    clusters = []

    sleeper_candidates = []
    for wallet in wallets:
        profile = wallet_profiles.get(wallet, {})
        first_seen = profile.get("first_seen")
        tx_count = profile.get("transaction_count", 0)

        if first_seen and tx_count < 5:
            days_since_creation = (datetime.now() - first_seen).days
            if days_since_creation <= 30:
                sleeper_candidates.append((wallet, first_seen))

    sleeper_candidates.sort(key=lambda x: x[1])

    current_group = []
    for wallet, creation_time in sleeper_candidates:
        if not current_group:
            current_group.append((wallet, creation_time))
        else:
            last_creation = current_group[-1][1]
            if (creation_time - last_creation).total_seconds() <= 3600:
                current_group.append((wallet, creation_time))
            else:
                if len(current_group) >= 3:
                    cluster_wallets = {w for w, _ in current_group}
                    cluster = WalletCluster(
                        cluster_id=f"sleeper_{len(clusters)}",
                        cluster_type=ClusterType.SLEEPER,
                        confidence=0.5,
                        wallets=cluster_wallets,
                        detection_signals=[
                            ClusterSignal(
                                signal_type="sleeper_pattern",
                                strength=0.5,
                                evidence={"creation_window_hours": 1},
                                description="Wallets created together, low activity - potential sleeper cluster",
                            )
                        ],
                        detection_method="sleeper_detection",
                    )
                    clusters.append(cluster)
                current_group = [(wallet, creation_time)]

    return clusters
