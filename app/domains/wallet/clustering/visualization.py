"""
Wallet clustering visualization — bubble map and AI forensic breakdown.

Generates D3.js-compatible bubble map data and AI-ready forensic context
for deep wallet cluster analysis.
"""

from __future__ import annotations
from datetime import datetime
from typing import Any

from app.domains.wallet.clustering.models import Cluster, Connection, WalletProfile

def get_connections_for_bubble_map(
    connections: dict[tuple[str, str], Connection],
    wallets: dict[str, WalletProfile],
    center_wallet: str,
    depth: int = 2,
    min_strength: float = 0.3,
    max_wallets: int = 250,
) -> tuple[list[str], list[Connection]]:
    """Get connections for bubble map visualization."""
    # Calculate connection strengths
    for conn in connections.values():
        count_score = min(1.0, conn.transaction_count / 100)
        volume_score = min(1.0, conn.total_volume / 10000)
        time_score = 0.5
        if conn.first_connection and conn.last_connection:
            duration = (conn.last_connection - conn.first_connection).days
            time_score = min(1.0, duration / 30)
        conn.strength = count_score * 0.4 + volume_score * 0.4 + time_score * 0.2

    all_wallets_set = {center_wallet}
    relevant_connections = []
    current_level = {center_wallet}

    for _d in range(depth):
        if len(all_wallets_set) >= max_wallets:
            break
        next_level = set()
        for wallet in current_level:
            for pair, conn in connections.items():
                if wallet in pair and conn.strength >= min_strength:
                    other = pair[1] if pair[0] == wallet else pair[0]
                    if other not in all_wallets_set:
                        if len(all_wallets_set) < max_wallets:
                            next_level.add(other)
                            all_wallets_set.add(other)
                        if conn not in relevant_connections:
                            relevant_connections.append(conn)
        current_level = next_level
        if not current_level:
            break

    return list(all_wallets_set), relevant_connections

def generate_bubble_map_data(
    connections: dict[tuple[str, str], Connection],
    wallets: dict[str, WalletProfile],
    center_wallet: str,
    depth: int = 2,
    max_wallets: int = 250,
) -> dict:
    """Generate data for interactive bubble map visualization."""
    wallet_list, relevant_connections = get_connections_for_bubble_map(
        connections, wallets, center_wallet, depth, max_wallets=max_wallets
    )

    nodes = []
    for _i, wallet in enumerate(wallet_list):
        profile = wallets.get(wallet)

        if wallet == center_wallet:
            node_type = "center"
            color = "#ff6b6b"
        elif wallet in _get_known_scammer_wallets():
            node_type = "scammer"
            color = "#ff0000"
        elif profile and len(profile.unique_counterparties) > 50:
            node_type = "exchange"
            color = "#4dabf7"
        else:
            node_type = "wallet"
            color = "#69db7c"

        volume = profile.total_volume if profile else 0
        size = min(50, max(10, volume / 100))

        nodes.append({
            "id": wallet,
            "type": node_type,
            "size": size,
            "color": color,
            "volume": volume,
            "transactions": profile.total_transactions if profile else 0,
            "label": f"{wallet[:8]}...",
        })

    links = []
    for conn in relevant_connections:
        links.append({
            "source": conn.wallet_a,
            "target": conn.wallet_b,
            "strength": round(conn.strength, 3),
            "volume": conn.total_volume,
            "transactions": conn.transaction_count,
            "value": conn.strength * 10,
        })

    return {
        "center_wallet": center_wallet,
        "depth": depth,
        "nodes": nodes,
        "links": links,
        "total_wallets": len(nodes),
        "total_connections": len(links),
        "generated_at": datetime.now().isoformat(),
    }

def _get_known_scammer_wallets() -> set[str]:
    """Get set of known scammer wallets (placeholder)."""
    return set()

def generate_ai_forensic_breakdown(
    connections: dict[tuple[str, str], Connection],
    wallets: dict[str, WalletProfile],
    transactions: list,
    center_wallet: str,
    initial_depth: int = 2,
    initial_max_wallets: int = 250,
    max_expansion_depth: int = 5,
    absolute_max_wallets: int = 1000,
) -> dict:
    """AI-driven forensic breakdown that dynamically pulls deeper if necessary."""
    initial_wallets, initial_connections = get_connections_for_bubble_map(
        connections, wallets, center_wallet, depth=initial_depth, max_wallets=initial_max_wallets
    )

    risk_score = 0.0
    risk_vectors = []

    intermediate_count = sum(1 for w in initial_wallets if w != center_wallet)
    if intermediate_count > 50:
        risk_score += 0.3
        risk_vectors.append("Complex layering detected (>50 intermediate wallets)")

    total_txs = sum(wallets[w].total_transactions for w in initial_wallets if w in wallets)
    if total_txs > 500:
        risk_score += 0.4
        risk_vectors.append("High transaction velocity detected")

    funding_sources = set()
    for w in initial_wallets:
        profile = wallets.get(w)
        if profile and profile.total_transactions > 0:
            funding_sources.update(profile.unique_counterparties)
    if len(funding_sources) < len(initial_wallets) * 0.5:
        risk_score += 0.3
        risk_vectors.append("Concentrated funding sources detected (potential sybil cluster)")

    needs_expansion = risk_score >= 0.5
    expanded_wallets = initial_wallets
    expanded_connections = initial_connections
    expansion_depth_used = initial_depth

    if needs_expansion:
        expanded_wallets, expanded_connections = get_connections_for_bubble_map(
            connections, wallets, center_wallet, depth=max_expansion_depth, max_wallets=absolute_max_wallets
        )
        expansion_depth_used = max_expansion_depth
        risk_vectors.append(
            f"AI auto-expanded analysis to depth {expansion_depth_used} "
            f"({len(expanded_wallets)} wallets) due to high risk indicators"
        )

    wallet_profiles = []
    for w in expanded_wallets:
        profile = wallets.get(w)
        if profile:
            wallet_profiles.append({
                "address": w,
                "is_center": w == center_wallet,
                "total_transactions": profile.total_transactions,
                "total_volume": profile.total_volume,
                "unique_counterparties_count": len(profile.unique_counterparties),
                "first_seen": profile.first_seen.isoformat() if profile.first_seen else None,
                "last_seen": profile.last_seen.isoformat() if profile.last_seen else None,
                "preferred_hours": profile.preferred_hours,
            })

    connection_summary = []
    for conn in expanded_connections:
        connection_summary.append({
            "source": conn.wallet_a,
            "target": conn.wallet_b,
            "strength": round(conn.strength, 3),
            "total_volume": conn.total_volume,
            "transaction_count": conn.transaction_count,
        })

    return {
        "center_wallet": center_wallet,
        "analysis_mode": "expanded_deep_forensics" if needs_expansion else "standard_cluster",
        "risk_score": round(risk_score, 2),
        "risk_vectors": risk_vectors,
        "expansion_triggered": needs_expansion,
        "depth_used": expansion_depth_used,
        "total_wallets_analyzed": len(expanded_wallets),
        "total_connections_analyzed": len(expanded_connections),
        "wallet_profiles": wallet_profiles,
        "connection_summary": connection_summary,
        "ai_prompt_context": (
            f"Analyze this wallet cluster centered on {center_wallet}. "
            f"Risk score: {risk_score:.2f}. "
            f"Vectors: {', '.join(risk_vectors)}. "
            f"The cluster contains {len(expanded_wallets)} wallets and {len(expanded_connections)} connections. "
            f"Identify the ultimate beneficiary, obfuscation tactics, and provide a clear forensic breakdown."
        ),
        "generated_at": datetime.now().isoformat(),
    }

def get_cluster_report(cluster: Cluster, wallets: dict[str, WalletProfile], connections: dict) -> dict | None:
    """Get detailed report for a cluster."""
    if not cluster:
        return None

    wallet_details = []
    for wallet in cluster.wallets:
        profile = wallets.get(wallet)
        if profile:
            wallet_details.append({
                "address": wallet,
                "transactions": profile.total_transactions,
                "volume": profile.total_volume,
                "counterparties": len(profile.unique_counterparties),
                "first_seen": profile.first_seen.isoformat() if profile.first_seen else None,
                "last_seen": profile.last_seen.isoformat() if profile.last_seen else None,
            })

    report = cluster.to_dict()
    report["wallet_details"] = wallet_details
    report["internal_connections"] = len([
        conn for conn in connections.values()
        if conn.wallet_a in cluster.wallets and conn.wallet_b in cluster.wallets
    ])
    return report

__all__ = [
    "get_connections_for_bubble_map",
    "generate_bubble_map_data",
    "generate_ai_forensic_breakdown",
    "get_cluster_report",
]
