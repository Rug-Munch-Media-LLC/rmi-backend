import logging

logger = logging.getLogger(__name__)
"""
Wallet clustering engine — identifies related addresses via on-chain behavior.

Part of the wallet domain. Detects wallet clusters using multiple forensic signals:
  - Transaction pattern analysis
  - Temporal proximity detection
  - Common counterparty identification
  - Fund flow tracing
  - Behavioral fingerprinting

Uses Neo4j graph DB for entity resolution storage.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Transaction:
    """Represents a blockchain transaction."""

    signature: str
    timestamp: datetime
    from_address: str
    to_address: str
    amount: float
    token: str
    program: str
    success: bool = True

    @property
    def is_transfer(self) -> bool:
        return self.program in ["spl-token", "system", "transfer"]


@dataclass
class WalletProfile:
    """Profile of a wallet's behavior."""

    address: str
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    total_transactions: int = 0
    total_volume: float = 0.0
    unique_counterparties: set[str] = field(default_factory=set)
    token_holdings: dict[str, float] = field(default_factory=dict)
    transaction_times: list[datetime] = field(default_factory=list)
    programs_used: set[str] = field(default_factory=set)

    # Behavioral metrics
    avg_transaction_size: float = 0.0
    transaction_frequency: float = 0.0  # tx per day
    preferred_hours: list[int] = field(default_factory=list)  # Hours of day most active

    def calculate_metrics(self):
        """Calculate behavioral metrics from transaction data."""
        if self.transaction_times:
            self.transaction_times.sort()
            self.first_seen = self.transaction_times[0]
            self.last_seen = self.transaction_times[-1]

            # Calculate frequency
            days_active = (self.last_seen - self.first_seen).days + 1
            if days_active > 0:
                self.transaction_frequency = len(self.transaction_times) / days_active

            # Preferred hours
            hours = [t.hour for t in self.transaction_times]
            hour_counts = defaultdict(int)
            for h in hours:
                hour_counts[h] += 1
            self.preferred_hours = sorted(
                hour_counts.keys(), key=lambda x: hour_counts[x], reverse=True
            )[:3]


@dataclass
class Cluster:
    """A detected wallet cluster."""

    cluster_id: str
    wallets: set[str]
    confidence: float
    detection_methods: list[str]
    center_wallet: str | None = None
    total_volume: float = 0.0
    common_tokens: set[str] = field(default_factory=set)
    common_counterparties: set[str] = field(default_factory=set)
    first_activity: datetime | None = None
    last_activity: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "wallets": list(self.wallets),
            "wallet_count": len(self.wallets),
            "confidence": round(self.confidence, 3),
            "detection_methods": self.detection_methods,
            "center_wallet": self.center_wallet,
            "total_volume": self.total_volume,
            "common_tokens": list(self.common_tokens),
            "common_counterparties": list(self.common_counterparties),
            "first_activity": self.first_activity.isoformat() if self.first_activity else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
        }


@dataclass
class Connection:
    """Connection between two wallets."""

    wallet_a: str
    wallet_b: str
    strength: float  # 0-1
    connection_types: list[str]
    evidence: list[dict]
    total_volume: float = 0.0
    transaction_count: int = 0
    first_connection: datetime | None = None
    last_connection: datetime | None = None


class WalletClusteringEngine:
    """
    Advanced wallet clustering engine using multiple forensic signals.
    """

    # Thresholds for clustering
    TEMPORAL_PROXIMITY_MINUTES = 5  # Transactions within 5 min considered coordinated
    MIN_COMMON_COUNTERPARTIES = 3  # Min shared counterparties for cluster
    MIN_TRANSACTION_SIMILARITY = 0.7  # Min similarity score for pattern match
    MIN_CONNECTION_STRENGTH = 0.3  # Min strength for bubble map connection

    def __init__(self):
        self.wallets: dict[str, WalletProfile] = {}
        self.transactions: list[Transaction] = []
        self.connections: dict[tuple[str, str], Connection] = {}
        self.clusters: dict[str, Cluster] = {}

    def add_transaction(self, tx: Transaction):
        """Add a transaction to the engine."""
        self.transactions.append(tx)

        # Update sender profile
        if tx.from_address not in self.wallets:
            self.wallets[tx.from_address] = WalletProfile(address=tx.from_address)
        sender = self.wallets[tx.from_address]
        sender.total_transactions += 1
        sender.total_volume += tx.amount
        sender.unique_counterparties.add(tx.to_address)
        sender.transaction_times.append(tx.timestamp)
        sender.programs_used.add(tx.program)

        # Update receiver profile
        if tx.to_address not in self.wallets:
            self.wallets[tx.to_address] = WalletProfile(address=tx.to_address)
        receiver = self.wallets[tx.to_address]
        receiver.total_transactions += 1
        receiver.total_volume += tx.amount
        receiver.unique_counterparties.add(tx.from_address)
        receiver.transaction_times.append(tx.timestamp)
        receiver.programs_used.add(tx.program)

        # Update or create connection
        pair = tuple(sorted([tx.from_address, tx.to_address]))
        if pair not in self.connections:
            self.connections[pair] = Connection(
                wallet_a=pair[0], wallet_b=pair[1], strength=0.0, connection_types=[], evidence=[]
            )

        conn = self.connections[pair]
        conn.transaction_count += 1
        conn.total_volume += tx.amount
        conn.evidence.append(
            {
                "signature": tx.signature,
                "timestamp": tx.timestamp.isoformat(),
                "amount": tx.amount,
                "token": tx.token,
            }
        )
        if conn.first_connection is None or tx.timestamp < conn.first_connection:
            conn.first_connection = tx.timestamp
        if conn.last_connection is None or tx.timestamp > conn.last_connection:
            conn.last_connection = tx.timestamp

    def load_from_helius(self, helius_data: list[dict]):
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

        # Recalculate all metrics
        for wallet in self.wallets.values():
            wallet.calculate_metrics()

    def detect_temporal_clusters(self, time_window_minutes: int | None = None) -> list[Cluster]:
        """
        Detect clusters based on temporal proximity of transactions.
        Wallets that transact at the same time may be coordinated.
        """
        time_window = time_window_minutes or self.TEMPORAL_PROXIMITY_MINUTES
        clusters = []

        # Group transactions by time windows
        time_groups = defaultdict(list)
        for tx in self.transactions:
            if not tx.success:
                continue
            # Round to time window
            window_key = tx.timestamp.replace(
                minute=(tx.timestamp.minute // time_window) * time_window, second=0, microsecond=0
            )
            time_groups[window_key].append(tx)

        # Find wallets active in same time windows
        cluster_id = 0
        processed_wallets = set()

        for window, txs in time_groups.items():
            if len(txs) < 2:
                continue

            # Get all wallets in this window
            window_wallets = set()
            for tx in txs:
                window_wallets.add(tx.from_address)
                window_wallets.add(tx.to_address)

            # Skip if already processed
            unprocessed = window_wallets - processed_wallets
            if len(unprocessed) < 2:
                continue

            # Check for common patterns
            common_tokens = set()
            common_programs = set()
            for tx in txs:
                common_tokens.add(tx.token)
                common_programs.add(tx.program)

            # Create cluster if significant
            if len(unprocessed) >= 2:
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

    def detect_common_counterparty_clusters(self) -> list[Cluster]:
        """
        Detect clusters based on shared counterparties.
        Wallets that send/receive from the same addresses may be related.
        """
        clusters = []

        # Build counterparty -> wallets mapping
        counterparty_wallets = defaultdict(set)
        for wallet in self.wallets.values():
            for counterparty in wallet.unique_counterparties:
                counterparty_wallets[counterparty].add(wallet.address)

        # Find wallets sharing multiple counterparties
        wallet_pairs = defaultdict(set)
        for counterparty, wallets in counterparty_wallets.items():
            if len(wallets) < 2:
                continue
            wallet_list = list(wallets)
            for i in range(len(wallet_list)):
                for j in range(i + 1, len(wallet_list)):
                    pair = tuple(sorted([wallet_list[i], wallet_list[j]]))
                    wallet_pairs[pair].add(counterparty)

        # Group into clusters
        cluster_map = defaultdict(set)
        for (w1, w2), counterparties in wallet_pairs.items():
            if len(counterparties) >= self.MIN_COMMON_COUNTERPARTIES:
                cluster_map[w1].add(w2)
                cluster_map[w2].add(w1)

        # Find connected components
        visited = set()
        cluster_id = 0

        for wallet in cluster_map:
            if wallet in visited:
                continue

            # BFS to find connected wallets
            cluster_wallets = set()
            queue = [wallet]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                cluster_wallets.add(current)
                queue.extend(cluster_map[current] - visited)

            if len(cluster_wallets) >= 2:
                # Find common counterparties for this cluster
                common_cp = None
                for w in cluster_wallets:
                    if common_cp is None:
                        common_cp = self.wallets[w].unique_counterparties
                    else:
                        common_cp = common_cp & self.wallets[w].unique_counterparties

                cluster = Cluster(
                    cluster_id=f"counterparty_{cluster_id}",
                    wallets=cluster_wallets,
                    confidence=min(0.95, 0.6 + len(cluster_wallets) * 0.05),
                    detection_methods=["common_counterparties"],
                    common_counterparties=common_cp or set(),
                    center_wallet=self._find_center_wallet(cluster_wallets),
                )
                clusters.append(cluster)
                cluster_id += 1

        return clusters

    def detect_pattern_clusters(self) -> list[Cluster]:
        """
        Detect clusters based on similar transaction patterns.
        Similar behavior may indicate the same operator.
        """
        clusters = []

        # Calculate behavioral fingerprints
        fingerprints = {}
        for address, wallet in self.wallets.items():
            if wallet.total_transactions < 5:  # Need enough data
                continue

            fingerprint = {
                "avg_size": wallet.avg_transaction_size
                or (wallet.total_volume / wallet.total_transactions),
                "frequency": wallet.transaction_frequency,
                "preferred_hours": wallet.preferred_hours,
                "program_diversity": len(wallet.programs_used),
                "counterparty_count": len(wallet.unique_counterparties),
            }
            fingerprints[address] = fingerprint

        # Find similar fingerprints
        similarity_matrix = {}
        addresses = list(fingerprints.keys())

        for i in range(len(addresses)):
            for j in range(i + 1, len(addresses)):
                w1, w2 = addresses[i], addresses[j]
                sim = self._calculate_fingerprint_similarity(fingerprints[w1], fingerprints[w2])
                if sim >= self.MIN_TRANSACTION_SIMILARITY:
                    similarity_matrix[(w1, w2)] = sim

        # Group similar wallets
        cluster_map = defaultdict(set)
        for (w1, w2), sim in similarity_matrix.items():  # noqa: B007
            cluster_map[w1].add(w2)
            cluster_map[w2].add(w1)

        # Find connected components
        visited = set()
        cluster_id = 0

        for wallet in cluster_map:
            if wallet in visited:
                continue

            cluster_wallets = set()
            queue = [wallet]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                cluster_wallets.add(current)
                queue.extend(cluster_map[current] - visited)

            if len(cluster_wallets) >= 2:
                cluster = Cluster(
                    cluster_id=f"pattern_{cluster_id}",
                    wallets=cluster_wallets,
                    confidence=min(0.85, 0.5 + len(cluster_wallets) * 0.05),
                    detection_methods=["behavioral_pattern"],
                    center_wallet=self._find_center_wallet(cluster_wallets),
                )
                clusters.append(cluster)
                cluster_id += 1

        return clusters

    def detect_funding_clusters(self) -> list[Cluster]:
        """
        Detect clusters based on common funding sources.
        Wallets funded from the same source may be related.
        """
        clusters = []

        # Find funding transactions (first transaction to each wallet)
        funding_sources = {}
        for wallet in self.wallets.values():
            if wallet.transaction_times:
                first_tx_time = min(wallet.transaction_times)
                # Find first incoming transaction
                for tx in self.transactions:
                    if tx.to_address == wallet.address and tx.timestamp == first_tx_time:
                        funding_sources[wallet.address] = tx.from_address
                        break

        # Group by funding source
        source_wallets = defaultdict(set)
        for wallet, source in funding_sources.items():
            source_wallets[source].add(wallet)

        # Create clusters for wallets with same funder
        cluster_id = 0
        for source, wallets in source_wallets.items():
            if len(wallets) >= 2:
                cluster = Cluster(
                    cluster_id=f"funding_{cluster_id}",
                    wallets=wallets,
                    confidence=0.8 if len(wallets) >= 5 else 0.6,
                    detection_methods=["common_funding_source"],
                    center_wallet=source,
                    common_counterparties={source},
                )
                clusters.append(cluster)
                cluster_id += 1

        return clusters

    def find_all_clusters(self) -> list[Cluster]:
        """Run all clustering methods and merge results."""
        all_clusters = []

        # Run all detection methods
        all_clusters.extend(self.detect_temporal_clusters())
        all_clusters.extend(self.detect_common_counterparty_clusters())
        all_clusters.extend(self.detect_pattern_clusters())
        all_clusters.extend(self.detect_funding_clusters())

        # Merge overlapping clusters
        merged = self._merge_clusters(all_clusters)

        # Store and return
        for cluster in merged:
            self.clusters[cluster.cluster_id] = cluster

        return merged

    def _merge_clusters(self, clusters: list[Cluster]) -> list[Cluster]:
        """Merge clusters that share wallets."""
        if not clusters:
            return []

        # Build wallet -> clusters mapping
        wallet_clusters = defaultdict(set)
        for i, cluster in enumerate(clusters):
            for wallet in cluster.wallets:
                wallet_clusters[wallet].add(i)

        # Find connected cluster groups
        visited = set()
        merged_clusters = []

        for i, cluster in enumerate(clusters):  # noqa: B007
            if i in visited:
                continue

            # BFS to find all connected clusters
            group_indices = set()
            queue = [i]
            while queue:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                group_indices.add(current)

                # Find connected clusters through shared wallets
                for wallet in clusters[current].wallets:
                    for connected in wallet_clusters[wallet]:
                        if connected not in visited:
                            queue.append(connected)

            # Merge this group
            all_wallets = set()
            all_methods = set()
            all_tokens = set()
            all_counterparties = set()
            max_confidence = 0

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
                center_wallet=self._find_center_wallet(all_wallets),
            )
            merged_clusters.append(merged)

        return merged_clusters

    def _calculate_fingerprint_similarity(self, fp1: dict, fp2: dict) -> float:
        """Calculate similarity between two behavioral fingerprints."""
        scores = []

        # Average transaction size similarity (normalized)
        if fp1["avg_size"] > 0 and fp2["avg_size"] > 0:
            size_ratio = min(fp1["avg_size"], fp2["avg_size"]) / max(
                fp1["avg_size"], fp2["avg_size"]
            )
            scores.append(size_ratio)

        # Frequency similarity
        if fp1["frequency"] > 0 and fp2["frequency"] > 0:
            freq_ratio = min(fp1["frequency"], fp2["frequency"]) / max(
                fp1["frequency"], fp2["frequency"]
            )
            scores.append(freq_ratio)

        # Preferred hours overlap
        hours1 = set(fp1["preferred_hours"])
        hours2 = set(fp2["preferred_hours"])
        if hours1 and hours2:
            hour_overlap = len(hours1 & hours2) / len(hours1 | hours2)
            scores.append(hour_overlap)

        # Program diversity similarity
        if fp1["program_diversity"] > 0 and fp2["program_diversity"] > 0:
            prog_ratio = min(fp1["program_diversity"], fp2["program_diversity"]) / max(
                fp1["program_diversity"], fp2["program_diversity"]
            )
            scores.append(prog_ratio)

        return sum(scores) / len(scores) if scores else 0

    def _find_center_wallet(self, wallets: set[str]) -> str | None:
        """Find the most connected wallet in a cluster (center)."""
        if not wallets:
            return None

        max_connections = 0
        center = None

        for wallet in wallets:
            if wallet in self.wallets:
                connections = len(self.wallets[wallet].unique_counterparties & wallets)
                if connections > max_connections:
                    max_connections = connections
                    center = wallet

        return center or next(iter(wallets))

    def get_connections_for_bubble_map(
        self,
        center_wallet: str,
        depth: int = 2,
        min_strength: float | None = None,
        max_wallets: int = 250,
    ) -> tuple[list[str], list[Connection]]:
        """
        Get connections for bubble map visualization.
        Supports deep linking up to 250 wallets for advanced forensics.

        Returns:
            Tuple of (all_wallets, connections)
        """
        min_str = min_strength or self.MIN_CONNECTION_STRENGTH

        # Calculate connection strengths
        for conn in self.connections.values():
            # Strength based on transaction count and volume
            count_score = min(1.0, conn.transaction_count / 100)
            volume_score = min(1.0, conn.total_volume / 10000)
            time_score = 0.5  # Base score
            if conn.first_connection and conn.last_connection:
                duration = (conn.last_connection - conn.first_connection).days
                time_score = min(1.0, duration / 30)  # Longer = stronger

            conn.strength = count_score * 0.4 + volume_score * 0.4 + time_score * 0.2

        # BFS to find connected wallets up to depth, capped at max_wallets
        all_wallets = {center_wallet}
        relevant_connections = []
        current_level = {center_wallet}

        for _d in range(depth):
            if len(all_wallets) >= max_wallets:
                break
            next_level = set()
            for wallet in current_level:
                for pair, conn in self.connections.items():
                    if wallet in pair and conn.strength >= min_str:
                        other = pair[1] if pair[0] == wallet else pair[0]
                        if other not in all_wallets:
                            if len(all_wallets) < max_wallets:
                                next_level.add(other)
                                all_wallets.add(other)
                            if conn not in relevant_connections:
                                relevant_connections.append(conn)
            current_level = next_level
            if not current_level:
                break

        return list(all_wallets), relevant_connections

    def generate_bubble_map_data(
        self, center_wallet: str, depth: int = 2, max_wallets: int = 250
    ) -> dict:
        """
        Generate data for interactive bubble map visualization.
        Supports up to 250 wallets deep for comprehensive cluster analysis.

        Returns JSON-ready data structure for D3.js or similar.
        """
        wallets, connections = self.get_connections_for_bubble_map(
            center_wallet, depth, max_wallets=max_wallets
        )

        # Build nodes
        nodes = []
        for _i, wallet in enumerate(wallets):
            profile = self.wallets.get(wallet)

            # Determine node type
            if wallet == center_wallet:
                node_type = "center"
                color = "#ff6b6b"  # Red
            elif wallet in self._get_known_scammer_wallets():
                node_type = "scammer"
                color = "#ff0000"  # Dark red
            elif profile and len(profile.unique_counterparties) > 50:
                node_type = "exchange"
                color = "#4dabf7"  # Blue
            else:
                node_type = "wallet"
                color = "#69db7c"  # Green

            # Size based on volume
            volume = profile.total_volume if profile else 0
            size = min(50, max(10, volume / 100))

            nodes.append(
                {
                    "id": wallet,
                    "type": node_type,
                    "size": size,
                    "color": color,
                    "volume": volume,
                    "transactions": profile.total_transactions if profile else 0,
                    "label": f"{wallet[:8]}...",
                }
            )

        # Build links
        links = []
        for conn in connections:
            links.append(
                {
                    "source": conn.wallet_a,
                    "target": conn.wallet_b,
                    "strength": round(conn.strength, 3),
                    "volume": conn.total_volume,
                    "transactions": conn.transaction_count,
                    "value": conn.strength * 10,  # For D3 force simulation
                }
            )

        return {
            "center_wallet": center_wallet,
            "depth": depth,
            "nodes": nodes,
            "links": links,
            "total_wallets": len(nodes),
            "total_connections": len(links),
            "generated_at": datetime.now().isoformat(),
        }

    def _get_known_scammer_wallets(self) -> set[str]:
        """Get set of known scammer wallets."""
        # This would come from your database
        return set()  # Placeholder

    def generate_ai_forensic_breakdown(
        self,
        center_wallet: str,
        initial_depth: int = 2,
        initial_max_wallets: int = 250,
        max_expansion_depth: int = 5,
        absolute_max_wallets: int = 1000,
    ) -> dict:
        """
        AI-driven forensic breakdown that dynamically pulls deeper if necessary.

        This is a premium feature that analyzes the initial cluster for risk vectors.
        If complex layering, high-risk patterns, or obfuscation tactics are detected,
        it automatically expands the search depth (up to absolute_max_wallets) to
        provide a comprehensive forensic breakdown that competitors lack.

        Returns a rich, AI-ready context payload for LLM analysis.
        """
        # Step 1: Get initial cluster data
        initial_wallets, initial_connections = self.get_connections_for_bubble_map(
            center_wallet, depth=initial_depth, max_wallets=initial_max_wallets
        )

        # Step 2: Analyze for risk vectors that warrant deeper investigation
        risk_score = 0.0
        risk_vectors = []

        # Check for complex layering (many intermediate wallets)
        intermediate_count = sum(1 for w in initial_wallets if w != center_wallet)
        if intermediate_count > 50:
            risk_score += 0.3
            risk_vectors.append("Complex layering detected (>50 intermediate wallets)")

        # Check for high transaction velocity
        total_txs = sum(
            self.wallets[w].total_transactions for w in initial_wallets if w in self.wallets
        )
        if total_txs > 500:
            risk_score += 0.4
            risk_vectors.append("High transaction velocity detected")

        # Check for common funding sources (potential sybil or coordinated attack)
        funding_sources = set()
        for w in initial_wallets:
            profile = self.wallets.get(w)
            if profile and profile.total_transactions > 0:
                # Simplified: check if they share counterparties
                funding_sources.update(profile.unique_counterparties)

        if len(funding_sources) < len(initial_wallets) * 0.5:
            risk_score += 0.3
            risk_vectors.append("Concentrated funding sources detected (potential sybil cluster)")

        # Step 3: Dynamically expand if risk score is high
        needs_expansion = risk_score >= 0.5
        expanded_wallets = initial_wallets
        expanded_connections = initial_connections
        expansion_depth_used = initial_depth

        if needs_expansion:
            # Expand depth up to max_expansion_depth, capped at absolute_max_wallets
            expanded_wallets, expanded_connections = self.get_connections_for_bubble_map(
                center_wallet, depth=max_expansion_depth, max_wallets=absolute_max_wallets
            )
            expansion_depth_used = max_expansion_depth
            risk_vectors.append(
                f"AI auto-expanded analysis to depth {expansion_depth_used} ({len(expanded_wallets)} wallets) due to high risk indicators"
            )

        # Step 4: Build AI-ready forensic context
        wallet_profiles = []
        for w in expanded_wallets:
            profile = self.wallets.get(w)
            if profile:
                wallet_profiles.append(
                    {
                        "address": w,
                        "is_center": w == center_wallet,
                        "total_transactions": profile.total_transactions,
                        "total_volume": profile.total_volume,
                        "unique_counterparties_count": len(profile.unique_counterparties),
                        "first_seen": profile.first_seen.isoformat()
                        if profile.first_seen
                        else None,
                        "last_seen": profile.last_seen.isoformat() if profile.last_seen else None,
                        "preferred_hours": profile.preferred_hours,
                    }
                )

        connection_summary = []
        for conn in expanded_connections:
            connection_summary.append(
                {
                    "source": conn.wallet_a,
                    "target": conn.wallet_b,
                    "strength": round(conn.strength, 3),
                    "total_volume": conn.total_volume,
                    "transaction_count": conn.transaction_count,
                }
            )

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
            "ai_prompt_context": f"Analyze this wallet cluster centered on {center_wallet}. "
            f"Risk score: {risk_score:.2f}. "
            f"Vectors: {', '.join(risk_vectors)}. "
            f"The cluster contains {len(expanded_wallets)} wallets and {len(expanded_connections)} connections. "
            f"Identify the ultimate beneficiary, obfuscation tactics, and provide a clear forensic breakdown.",
            "generated_at": datetime.now().isoformat(),
        }

    def get_cluster_report(self, cluster_id: str) -> dict | None:
        """Get detailed report for a cluster."""
        cluster = self.clusters.get(cluster_id)
        if not cluster:
            return None

        # Get wallet details
        wallet_details = []
        for wallet in cluster.wallets:
            profile = self.wallets.get(wallet)
            if profile:
                wallet_details.append(
                    {
                        "address": wallet,
                        "transactions": profile.total_transactions,
                        "volume": profile.total_volume,
                        "counterparties": len(profile.unique_counterparties),
                        "first_seen": profile.first_seen.isoformat()
                        if profile.first_seen
                        else None,
                        "last_seen": profile.last_seen.isoformat() if profile.last_seen else None,
                    }
                )

        report = cluster.to_dict()
        report["wallet_details"] = wallet_details
        report["internal_connections"] = len(
            [
                conn
                for conn in self.connections.values()
                if conn.wallet_a in cluster.wallets and conn.wallet_b in cluster.wallets
            ]
        )

        return report


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
