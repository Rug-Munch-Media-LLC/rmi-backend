"""
Cluster Detection Pro - Detection Methods
==========================================
Standalone detection functions that identify wallet clusters using
various signals. Each function operates on a wallet_profiles dict
and returns a list of WalletCluster candidates.

Methods:
  1. Temporal Proximity  - Same-time activity windows
  2. Common Counterparties - Shared senders/recipients
  3. Behavioral Similarity - Same transaction patterns
  4. Common Funding      - Same funding source
  5. Transaction Patterns - Similar flows (stub)
  6. Machine Learning    - DBSCAN clustering
  7. Sleeper Detection   - Dormant wallet groups
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from app.cluster_detection.models import (
    ClusterSignal,
    ClusterType,
    WalletCluster,
)
from app.cluster_detection.scoring import (
    calculate_behavioral_similarity,
    distribution,
)


async def detect_temporal_clusters(
    wallet_profiles: dict[str, dict[str, Any]],
    wallets: list[str],
) -> list[WalletCluster]:
    """
    Detect clusters based on temporal proximity of transactions.
    Wallets active at the same time may be coordinated.
    """
    clusters: list[WalletCluster] = []

    # Group transactions by 5-minute windows
    time_windows = defaultdict(set)

    for wallet in wallets:
        profile = wallet_profiles.get(wallet, {})
        for tx in profile.get("transactions", []):
            timestamp = tx.get("timestamp")
            if timestamp:
                window = timestamp.replace(minute=(timestamp.minute // 5) * 5, second=0, microsecond=0)
                time_windows[window].add(wallet)

    # Find wallets appearing together frequently
    cooccurrence: defaultdict[str, defaultdict[str, int]] = defaultdict(lambda: defaultdict(int))

    for window, window_wallets in time_windows.items():
        if len(window_wallets) < 2:
            continue
        wallet_list = list(window_wallets)
        for i in range(len(wallet_list)):
            for j in range(i + 1, len(wallet_list)):
                cooccurrence[wallet_list[i]][wallet_list[j]] += 1
                cooccurrence[wallet_list[j]][wallet_list[i]] += 1

    # Build clusters from high co-occurrence pairs
    threshold = 3  # Minimum co-occurrences
    clustered = set()

    for wallet_a, connections in cooccurrence.items():
        if wallet_a in clustered:
            continue

        cluster_wallets = {wallet_a}
        for wallet_b, count in connections.items():
            if count >= threshold and wallet_b not in clustered:
                cluster_wallets.add(wallet_b)

        if len(cluster_wallets) >= 2:
            cluster = WalletCluster(
                cluster_id=f"temporal_{len(clusters)}",
                cluster_type=ClusterType.UNKNOWN,
                confidence=0.0,
                wallets=cluster_wallets,
                detection_signals=[
                    ClusterSignal(
                        signal_type="temporal_proximity",
                        strength=min(1.0, len(cluster_wallets) * 0.1),
                        evidence={"cooccurrence_threshold": threshold},
                        description=f"Wallets active together in {len(cluster_wallets)} time windows",
                    )
                ],
                detection_method="temporal_proximity",
            )
            clusters.append(cluster)
            clustered.update(cluster_wallets)

    return clusters


async def detect_counterparty_clusters(
    wallet_profiles: dict[str, dict[str, Any]],
    wallets: list[str],
) -> list[WalletCluster]:
    """
    Detect clusters based on shared counterparties.
    Wallets sending/receiving to same addresses may be related.
    """
    clusters: list[WalletCluster] = []

    # Build counterparty -> wallets mapping
    counterparty_wallets = defaultdict(set)

    for wallet in wallets:
        profile = wallet_profiles.get(wallet, {})
        for counterparty in profile.get("unique_counterparties", set()):
            counterparty_wallets[counterparty].add(wallet)

    # Find wallets sharing multiple counterparties
    shared_counterparties: defaultdict[str, defaultdict[str, set[str]]] = defaultdict(lambda: defaultdict(set))

    for counterparty, c_wallets in counterparty_wallets.items():
        if len(c_wallets) < 2:
            continue
        wallet_list = list(c_wallets)
        for i in range(len(wallet_list)):
            for j in range(i + 1, len(wallet_list)):
                shared_counterparties[wallet_list[i]][wallet_list[j]].add(counterparty)
                shared_counterparties[wallet_list[j]][wallet_list[i]].add(counterparty)

    # Build clusters (minimum 3 shared counterparties)
    min_shared = 3
    threshold = 5  # Minimum wallets in cluster
    clustered = set()

    for wallet_a, connections in shared_counterparties.items():
        if wallet_a in clustered:
            continue

        cluster_wallets = {wallet_a}
        common_cps = None

        for wallet_b, shared in connections.items():
            if len(shared) >= min_shared and wallet_b not in clustered:
                cluster_wallets.add(wallet_b)
                common_cps = shared if common_cps is None else common_cps & shared

        if len(cluster_wallets) >= threshold:
            cluster = WalletCluster(
                cluster_id=f"counterparty_{len(clusters)}",
                cluster_type=ClusterType.UNKNOWN,
                confidence=0.0,
                wallets=cluster_wallets,
                common_counterparties=common_cps or set(),
                detection_signals=[
                    ClusterSignal(
                        signal_type="common_counterparties",
                        strength=min(1.0, len(common_cps or set()) * 0.1),
                        evidence={"shared_counterparties": len(common_cps or set())},
                        description=f"Share {len(common_cps or set())} common counterparties",
                    )
                ],
                detection_method="common_counterparties",
            )
            clusters.append(cluster)
            clustered.update(cluster_wallets)

    return clusters


async def detect_behavioral_clusters(
    wallet_profiles: dict[str, dict[str, Any]],
    wallets: list[str],
) -> list[WalletCluster]:
    """
    Detect clusters based on behavioral similarity.
    Similar patterns may indicate same operator.
    """
    clusters: list[WalletCluster] = []

    # Extract behavioral fingerprints
    fingerprints = {}

    for wallet in wallets:
        profile = wallet_profiles.get(wallet, {})

        if profile.get("transaction_count", 0) < 10:
            continue

        # Calculate behavioral metrics
        txs = profile.get("transactions", [])

        if not txs:
            continue

        # Transaction size distribution
        amounts = [tx.get("amount", 0) for tx in txs]
        avg_amount = sum(amounts) / len(amounts) if amounts else 0

        # Timing patterns
        timestamps = [tx.get("timestamp") for tx in txs if tx.get("timestamp")]
        if timestamps:
            hours = [t.hour for t in timestamps]
            hour_dist = distribution(hours)
        else:
            hour_dist = [0] * 24

        # Program usage
        programs = {tx.get("program", "") for tx in txs}

        fingerprints[wallet] = {
            "avg_amount": avg_amount,
            "tx_count": len(txs),
            "hour_distribution": hour_dist,
            "program_count": len(programs),
        }

    if len(fingerprints) < 2:
        return clusters

    # Calculate similarity matrix
    similarity_matrix = {}
    wallet_list = list(fingerprints.keys())

    for i in range(len(wallet_list)):
        for j in range(i + 1, len(wallet_list)):
            w1, w2 = wallet_list[i], wallet_list[j]
            sim = calculate_behavioral_similarity(fingerprints[w1], fingerprints[w2])
            if sim > 0.7:  # Threshold
                similarity_matrix[(w1, w2)] = sim

    # Cluster using connected components
    graph = defaultdict(set)

    for (w1, w2), sim in similarity_matrix.items():
        graph[w1].add(w2)
        graph[w2].add(w1)

    visited = set()

    for wallet in graph:
        if wallet in visited:
            continue

        # BFS to find connected component
        component = set()
        queue = [wallet]

        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            queue.extend(graph[current] - visited)

        if len(component) >= 2:
            cluster = WalletCluster(
                cluster_id=f"behavioral_{len(clusters)}",
                cluster_type=ClusterType.UNKNOWN,
                confidence=0.0,
                wallets=component,
                detection_signals=[
                    ClusterSignal(
                        signal_type="behavioral_similarity",
                        strength=0.7,
                        evidence={"similarity_threshold": 0.7},
                        description="Similar transaction patterns and timing",
                    )
                ],
                detection_method="behavioral_similarity",
            )
            clusters.append(cluster)

    return clusters


async def detect_funding_clusters(
    wallet_profiles: dict[str, dict[str, Any]],
    wallets: list[str],
) -> list[WalletCluster]:
    """Detect clusters based on common funding sources."""
    clusters: list[WalletCluster] = []

    # Find funding transactions (first incoming tx)
    funding_sources = defaultdict(set)

    for wallet in wallets:
        profile = wallet_profiles.get(wallet, {})
        txs = profile.get("transactions", [])

        # Find first incoming transaction
        incoming = [tx for tx in txs if tx.get("to") == wallet]
        if incoming:
            first_tx = min(incoming, key=lambda x: x.get("timestamp", datetime.max))
            funder = first_tx.get("from")
            if funder:
                funding_sources[funder].add(wallet)

    # Create clusters for wallets with same funder
    for funder, funded_wallets in funding_sources.items():
        if len(funded_wallets) >= 2:
            cluster = WalletCluster(
                cluster_id=f"funding_{len(clusters)}",
                cluster_type=ClusterType.FUNDING,
                confidence=0.8,
                wallets=funded_wallets,
                center_wallet=funder,
                detection_signals=[
                    ClusterSignal(
                        signal_type="common_funding",
                        strength=0.8,
                        evidence={"funder": funder, "funded_count": len(funded_wallets)},
                        description=f"All funded by {funder[:16]}...",
                    )
                ],
                detection_method="common_funding",
            )
            clusters.append(cluster)

    return clusters
