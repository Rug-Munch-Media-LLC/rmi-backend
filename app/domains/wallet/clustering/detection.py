"""
Wallet clustering detection methods.

Provides the detection algorithms that WalletClusteringEngine uses:
  - temporal_proximity
  - common_counterparties
  - behavioral_patterns (fingerprint similarity)
  - common_funding_source
  - merge overlapping clusters
  - fingerprint similarity scoring
  - center wallet identification
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

from app.domains.wallet.clustering.models import Cluster, WalletProfile


def detect_temporal_clusters(
    wallets: dict[str, WalletProfile],
    transactions: list[Any],
    time_window_minutes: int = 5,
) -> list[Cluster]:
    """Detect clusters based on temporal proximity of transactions."""
    clusters: list[Cluster] = []
    time_groups: defaultdict[datetime, list[Any]] = defaultdict(list)
    for tx in transactions:
        if not tx.success:
            continue
        window_key = tx.timestamp.replace(
            minute=(tx.timestamp.minute // time_window_minutes) * time_window_minutes,
            second=0,
            microsecond=0,
        )
        time_groups[window_key].append(tx)

    cluster_id = 0
    processed_wallets = set()

    for window, txs in time_groups.items():
        if len(txs) < 2:
            continue
        window_wallets = set()
        for tx in txs:
            window_wallets.add(tx.from_address)
            window_wallets.add(tx.to_address)

        unprocessed = window_wallets - processed_wallets
        if len(unprocessed) < 2:
            continue

        common_tokens = {tx.token for tx in txs}

        cluster = Cluster(
            cluster_id=f"temporal_{cluster_id}",
            wallets=unprocessed,
            confidence=min(0.9, 0.5 + len(unprocessed) * 0.1),
            detection_methods=["temporal_proximity"],
            common_tokens=common_tokens,
            first_activity=window,
            last_activity=window,
        )
        clusters.append(cluster)
        processed_wallets.update(unprocessed)
        cluster_id += 1

    return clusters


def detect_common_counterparty_clusters(
    wallets: dict[str, WalletProfile],
    min_common_counterparties: int = 3,
) -> list[Cluster]:
    """Detect clusters based on shared counterparties."""
    clusters = []

    counterparty_wallets = defaultdict(set)
    for wallet in wallets.values():
        for counterparty in wallet.unique_counterparties:
            counterparty_wallets[counterparty].add(wallet.address)

    wallet_pairs = defaultdict(set)
    for counterparty, wallet_set in counterparty_wallets.items():
        if len(wallet_set) < 2:
            continue
        wallet_list = list(wallet_set)
        for i in range(len(wallet_list)):
            for j in range(i + 1, len(wallet_list)):
                pair = tuple(sorted([wallet_list[i], wallet_list[j]]))
                wallet_pairs[pair].add(counterparty)

    cluster_map: defaultdict[str, set[str]] = defaultdict(set)
    for (w1, w2), counterparties in wallet_pairs.items():
        if len(counterparties) >= min_common_counterparties:
            cluster_map[w1].add(w2)
            cluster_map[w2].add(w1)

    visited = set()
    cluster_id = 0
    for wallet_addr in cluster_map:
        if wallet_addr in visited:
            continue
        cluster_wallets = set()
        queue = [wallet_addr]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            cluster_wallets.add(current)
            queue.extend(cluster_map[current] - visited)

        if len(cluster_wallets) >= 2:
            common_cp = None
            for w in cluster_wallets:
                if common_cp is None:
                    common_cp = wallets[w].unique_counterparties
                else:
                    common_cp = common_cp & wallets[w].unique_counterparties

            clusters.append(
                Cluster(
                    cluster_id=f"counterparty_{cluster_id}",
                    wallets=cluster_wallets,
                    confidence=min(0.95, 0.6 + len(cluster_wallets) * 0.05),
                    detection_methods=["common_counterparties"],
                    common_counterparties=common_cp or set(),
                    center_wallet=_find_center_wallet(cluster_wallets, wallets),
                )
            )
            cluster_id += 1

    return clusters


def detect_pattern_clusters(
    wallets: dict[str, WalletProfile],
    min_transaction_similarity: float = 0.7,
) -> list[Cluster]:
    """Detect clusters based on similar transaction patterns."""
    clusters = []

    fingerprints = {}
    for address, wallet in wallets.items():
        if wallet.total_transactions < 5:
            continue
        fingerprints[address] = {
            "avg_size": wallet.avg_transaction_size or (wallet.total_volume / wallet.total_transactions),
            "frequency": wallet.transaction_frequency,
            "preferred_hours": wallet.preferred_hours,
            "program_diversity": len(wallet.programs_used),
            "counterparty_count": len(wallet.unique_counterparties),
        }

    similarity_matrix = {}
    addresses = list(fingerprints.keys())
    for i in range(len(addresses)):
        for j in range(i + 1, len(addresses)):
            sim = _calculate_fingerprint_similarity(fingerprints[addresses[i]], fingerprints[addresses[j]])
            if sim >= min_transaction_similarity:
                similarity_matrix[(addresses[i], addresses[j])] = sim

    cluster_map: defaultdict[str, set[str]] = defaultdict(set)
    for w1, w2 in similarity_matrix:
        cluster_map[w1].add(w2)
        cluster_map[w2].add(w1)

    visited = set()
    cluster_id = 0
    for wallet_addr in cluster_map:
        if wallet_addr in visited:
            continue
        cluster_wallets = set()
        queue = [wallet_addr]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            cluster_wallets.add(current)
            queue.extend(cluster_map[current] - visited)

        if len(cluster_wallets) >= 2:
            clusters.append(
                Cluster(
                    cluster_id=f"pattern_{cluster_id}",
                    wallets=cluster_wallets,
                    confidence=min(0.85, 0.5 + len(cluster_wallets) * 0.05),
                    detection_methods=["behavioral_pattern"],
                    center_wallet=_find_center_wallet(cluster_wallets, wallets),
                )
            )
            cluster_id += 1

    return clusters


def detect_funding_clusters(
    wallets: dict[str, WalletProfile],
    transactions: list[Any],
) -> list[Cluster]:
    """Detect clusters based on common funding sources."""
    clusters = []

    funding_sources = {}
    for wallet in wallets.values():
        if wallet.transaction_times:
            first_tx_time = min(wallet.transaction_times)
            for tx in transactions:
                if tx.to_address == wallet.address and tx.timestamp == first_tx_time:
                    funding_sources[wallet.address] = tx.from_address
                    break

    source_wallets = defaultdict(set)
    for wallet_addr, source in funding_sources.items():
        source_wallets[source].add(wallet_addr)

    cluster_id = 0
    for source, wallet_set in source_wallets.items():
        if len(wallet_set) >= 2:
            clusters.append(
                Cluster(
                    cluster_id=f"funding_{cluster_id}",
                    wallets=wallet_set,
                    confidence=0.8 if len(wallet_set) >= 5 else 0.6,
                    detection_methods=["common_funding_source"],
                    center_wallet=source,
                    common_counterparties={source},
                )
            )
            cluster_id += 1

    return clusters


def merge_clusters(clusters: list[Cluster]) -> list[Cluster]:
    """Merge clusters that share wallets."""
    if not clusters:
        return []

    wallet_clusters = defaultdict(set)
    for i, cluster in enumerate(clusters):
        for wallet in cluster.wallets:
            wallet_clusters[wallet].add(i)

    visited = set()
    merged_clusters = []

    for i in range(len(clusters)):
        if i in visited:
            continue

        group_indices = set()
        queue = [i]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            group_indices.add(current)
            for wallet in clusters[current].wallets:
                for connected in wallet_clusters[wallet]:
                    if connected not in visited:
                        queue.append(connected)

        all_wallets = set()
        all_methods = set()
        all_tokens = set()
        all_counterparties = set()
        max_confidence = 0.0

        for idx in group_indices:
            c = clusters[idx]
            all_wallets.update(c.wallets)
            all_methods.update(c.detection_methods)
            all_tokens.update(c.common_tokens)
            all_counterparties.update(c.common_counterparties)
            max_confidence = max(max_confidence, c.confidence)

        merged = Cluster(
            cluster_id=f"merged_{len(merged_clusters)}",
            wallets=all_wallets,
            confidence=min(0.98, max_confidence + len(all_methods) * 0.05),
            detection_methods=list(all_methods),
            common_tokens=all_tokens,
            common_counterparties=all_counterparties,
            center_wallet=_find_center_wallet(all_wallets, {}),
        )
        merged_clusters.append(merged)

    return merged_clusters


def _calculate_fingerprint_similarity(fp1: dict[str, Any], fp2: dict[str, Any]) -> float:
    """Calculate similarity between two behavioral fingerprints."""
    scores = []

    if fp1["avg_size"] > 0 and fp2["avg_size"] > 0:
        size_ratio = min(fp1["avg_size"], fp2["avg_size"]) / max(fp1["avg_size"], fp2["avg_size"])
        scores.append(size_ratio)

    if fp1["frequency"] > 0 and fp2["frequency"] > 0:
        freq_ratio = min(fp1["frequency"], fp2["frequency"]) / max(fp1["frequency"], fp2["frequency"])
        scores.append(freq_ratio)

    hours1 = set(fp1["preferred_hours"])
    hours2 = set(fp2["preferred_hours"])
    if hours1 and hours2:
        hour_overlap = len(hours1 & hours2) / len(hours1 | hours2)
        scores.append(hour_overlap)

    if fp1["program_diversity"] > 0 and fp2["program_diversity"] > 0:
        prog_ratio = min(fp1["program_diversity"], fp2["program_diversity"]) / max(
            fp1["program_diversity"], fp2["program_diversity"]
        )
        scores.append(prog_ratio)

    return sum(scores) / len(scores) if scores else 0


def _find_center_wallet(wallets: set[str], wallets_dict: dict[str, Any]) -> str | None:
    """Find the most connected wallet in a cluster (center)."""
    if not wallets:
        return None
    # Simple fallback: return first wallet
    if not wallets_dict:
        return next(iter(wallets))

    max_connections = 0
    center = None
    for wallet in wallets:
        if wallet in wallets_dict:
            connections = len(wallets_dict[wallet].unique_counterparties & wallets)
            if connections > max_connections:
                max_connections = connections
                center = wallet
    return center or next(iter(wallets))


__all__ = [
    "_calculate_fingerprint_similarity",
    "_find_center_wallet",
    "detect_common_counterparty_clusters",
    "detect_funding_clusters",
    "detect_pattern_clusters",
    "detect_temporal_clusters",
    "merge_clusters",
]
