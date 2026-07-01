"""
Wallet Clustering Engine — 6 heuristics with Union-Find merge.
================================================================
Upgraded from the original 4-heuristic engine in wallet_clustering.py.
Added: gas payer linkage (EVM deployer networks) and deposit-withdraw patterns (CEX tracing).

Heuristics (confidence 0-1):
  1. Temporal proximity        — 0.60  (coordinated tx timing)
  2. Common counterparties     — 0.70  (shared senders/recipients)
  3. Behavioral patterns      — 0.65  (similar fingerprint)
  4. Common funding source     — 0.80  (same funder = same operator)
  5. Gas payer linkage         — 0.85  (one wallet pays for multiple deploys)
  6. Deposit-withdraw pattern  — 0.80  (exchange flow tracing)

Union-Find for O(α(n)) merges. Results persisted to storage.py.
"""

import logging
from collections import defaultdict
from datetime import datetime

logger = logging.getLogger("wallet_memory.clustering")


# ── Union-Find (Disjoint Set) ────────────────────────────────────────


class UnionFind:
    """Weighted Union-Find with path compression. O(α(n)) per op."""

    def __init__(self):
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}
        self._size: dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
            self._size[x] = 1
            return x
        # Path compression
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: str, y: str) -> str:
        """Merge sets containing x and y. Returns root of merged set."""
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return rx

        # Weighted union by rank
        if self._rank[rx] < self._rank[ry]:
            rx, ry = ry, rx
        self._parent[ry] = rx
        self._size[rx] += self._size[ry]
        if self._rank[rx] == self._rank[ry]:
            self._rank[rx] += 1
        return rx

    def connected(self, x: str, y: str) -> bool:
        return self.find(x) == self.find(y)

    def get_members(self, x: str) -> set[str]:
        """Get all members of x's set. O(n) — use sparingly."""
        root = self.find(x)
        return {k for k, v in self._parent.items() if v and self.find(k) == root}

    def get_all_clusters(self) -> dict[str, set[str]]:
        """Return {root: set_of_members} for all clusters."""
        clusters: dict[str, set[str]] = defaultdict(set)
        for key in self._parent:
            root = self.find(key)
            clusters[root].add(key)
        return dict(clusters)

    @property
    def total_nodes(self) -> int:
        return len(self._parent)


# ── Data classes ─────────────────────────────────────────────────────


class WalletProfile:
    """Behavioral profile of a wallet."""

    __slots__ = (
        "address",
        "avg_tx_size",
        "chain_id",
        "first_seen",
        "last_seen",
        "preferred_hours",
        "programs_used",
        "total_transactions",
        "total_volume",
        "transaction_times",
        "tx_frequency",
        "unique_counterparties",
    )

    def __init__(self, address: str, chain_id: str = ""):
        self.address = address
        self.chain_id = chain_id
        self.first_seen: datetime | None = None
        self.last_seen: datetime | None = None
        self.total_transactions = 0
        self.total_volume = 0.0
        self.unique_counterparties: set[str] = set()
        self.transaction_times: list[datetime] = []
        self.programs_used: set[str] = set()
        self.preferred_hours: list[int] = []
        self.avg_tx_size = 0.0
        self.tx_frequency = 0.0

    def add_tx(self, timestamp: datetime, counterparty: str, amount: float = 0.0, program: str = ""):
        self.total_transactions += 1
        self.total_volume += amount
        self.unique_counterparties.add(counterparty)
        self.transaction_times.append(timestamp)
        if program:
            self.programs_used.add(program)

    def calculate_metrics(self):
        if not self.transaction_times:
            return
        self.transaction_times.sort()
        self.first_seen = self.transaction_times[0]
        self.last_seen = self.transaction_times[-1]
        days = max((self.last_seen - self.first_seen).days, 1)
        self.tx_frequency = len(self.transaction_times) / days
        if self.total_transactions > 0:
            self.avg_tx_size = self.total_volume / self.total_transactions
        hours = [t.hour for t in self.transaction_times]
        hour_counts = defaultdict(int)
        for h in hours:
            hour_counts[h] += 1
        self.preferred_hours = sorted(hour_counts.keys(), key=lambda h: hour_counts[h], reverse=True)[:3]


class Cluster:
    """A detected wallet cluster (entity)."""

    def __init__(self, cluster_id: str):
        self.cluster_id = cluster_id
        self.wallets: set[str] = set()
        self.confidence: float = 0.0
        self.detection_methods: list[str] = []
        self.center_wallet: str | None = None
        self.label: str = ""
        self.category: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "wallet_count": len(self.wallets),
            "wallets": list(self.wallets),
            "confidence": round(self.confidence, 3),
            "detection_methods": self.detection_methods,
            "center_wallet": self.center_wallet,
            "label": self.label,
            "category": self.category,
        }


# ── Clustering Engine ───────────────────────────────────────────────

HEURISTIC_WEIGHTS = {
    "temporal_proximity": 0.60,
    "common_counterparties": 0.70,
    "behavioral_pattern": 0.65,
    "common_funding": 0.80,
    "gas_payer": 0.85,
    "deposit_withdraw": 0.80,
}


class WalletClusteringEngine:
    """6-heuristic clustering engine with Union-Find merge."""

    TEMPORAL_WINDOW_MINUTES = 5
    MIN_COMMON_COUNTERPARTIES = 3
    MIN_FINGERPRINT_SIMILARITY = 0.7
    MIN_GAS_PAYER_RECIPIENTS = 3

    def __init__(self):
        self.wallets: dict[str, WalletProfile] = {}
        self.uf = UnionFind()
        self._heuristic_links: dict[str, set[str]] = defaultdict(set)
        # Track which heuristic linked each pair
        self._pair_heuristics: dict[tuple[str, str], set[str]] = defaultdict(set)

    def add_transaction(
        self,
        address: str,
        counterparty: str,
        timestamp: datetime,
        amount: float = 0.0,
        chain_id: str = "",
        program: str = "",
    ):
        """Feed a transaction into the engine."""
        addr = address.lower()
        cp = counterparty.lower()

        if addr not in self.wallets:
            self.wallets[addr] = WalletProfile(addr, chain_id)
        self.wallets[addr].add_tx(timestamp, cp, amount, program)

        # Ensure both exist in Union-Find
        self.uf.find(addr)
        self.uf.find(cp)

    # ── Heuristic 1: Temporal Proximity ──────────────────────────

    def detect_temporal_clusters(self, time_window_min: int | None = None) -> list[Cluster]:
        """Wallets transacting in the same time window may be coordinated."""
        window = time_window_min or self.TEMPORAL_WINDOW_MINUTES
        time_groups: dict[datetime, set[str]] = defaultdict(set)

        for addr, profile in self.wallets.items():
            for ts in profile.transaction_times:
                key = ts.replace(
                    minute=(ts.minute // window) * window,
                    second=0,
                    microsecond=0,
                )
                time_groups[key].add(addr)

        clusters = []
        for window_time, addrs in time_groups.items():
            if len(addrs) < 2:
                continue
            # Link all wallets in this window
            addr_list = list(addrs)
            for i in range(len(addr_list)):
                for j in range(i + 1, len(addr_list)):
                    pair = tuple(sorted([addr_list[i], addr_list[j]]))
                    self._pair_heuristics[pair].add("temporal_proximity")
                    self._heuristic_links["temporal_proximity"].add(f"{pair[0]}:{pair[1]}")
                    self.uf.union(addr_list[i], addr_list[j])

            c = Cluster(f"temporal_{window_time.isoformat()}")
            c.wallets = addrs
            c.detection_methods = ["temporal_proximity"]
            c.confidence = min(0.60, 0.3 + len(addrs) * 0.05)
            clusters.append(c)

        return clusters

    # ── Heuristic 2: Common Counterparties ────────────────────────

    def detect_counterparty_clusters(self) -> list[Cluster]:
        """Wallets sharing multiple counterparties may be related."""
        # Map: counterparty -> set of wallets that interacted with it
        cp_wallets: dict[str, set[str]] = defaultdict(set)
        for addr, profile in self.wallets.items():
            for cp in profile.unique_counterparties:
                cp_wallets[cp].add(addr)

        # Find pairs sharing >= MIN_COMMON_COUNTERPARTIES
        pair_shared: dict[tuple[str, str], int] = defaultdict(int)
        for cp, wallets in cp_wallets.items():
            wl = list(wallets)
            for i in range(len(wl)):
                for j in range(i + 1, len(wl)):
                    pair = tuple(sorted([wl[i], wl[j]]))
                    pair_shared[pair] += 1

        # Link pairs exceeding threshold
        for pair, count in pair_shared.items():
            if count >= self.MIN_COMMON_COUNTERPARTIES:
                self._pair_heuristics[pair].add("common_counterparties")
                self._heuristic_links["common_counterparties"].add(f"{pair[0]}:{pair[1]}")
                self.uf.union(pair[0], pair[1])

        # Return cluster view
        raw = self.uf.get_all_clusters()
        clusters = []
        for root, members in raw.items():
            # Only include if counterparty heuristic contributed
            counterparty_pairs = [
                (a, b)
                for a in members
                for b in members
                if a < b and "common_counterparties" in self._pair_heuristics.get(tuple(sorted([a, b])), set())
            ]
            if counterparty_pairs and len(members) >= 2:
                c = Cluster(f"counterparty_{root[:12]}")
                c.wallets = members
                c.detection_methods = ["common_counterparties"]
                c.confidence = min(0.70, 0.4 + len(members) * 0.05)
                c.center_wallet = self._find_center(members)
                clusters.append(c)
        return clusters

    # ── Heuristic 3: Behavioral Fingerprint ───────────────────────

    def detect_behavioral_clusters(self) -> list[Cluster]:
        """Wallets with similar behavioral fingerprints may share an operator."""
        fingerprints = {}
        for addr, profile in self.wallets.items():
            if profile.total_transactions < 5:
                continue
            profile.calculate_metrics()
            fingerprints[addr] = {
                "avg_size": profile.avg_tx_size,
                "frequency": profile.tx_frequency,
                "preferred_hours": set(profile.preferred_hours),
                "program_diversity": len(profile.programs_used),
                "counterparty_count": len(profile.unique_counterparties),
            }

        addrs = list(fingerprints.keys())
        for i in range(len(addrs)):
            for j in range(i + 1, len(addrs)):
                sim = self._fingerprint_similarity(fingerprints[addrs[i]], fingerprints[addrs[j]])
                if sim >= self.MIN_FINGERPRINT_SIMILARITY:
                    pair = tuple(sorted([addrs[i], addrs[j]]))
                    self._pair_heuristics[pair].add("behavioral_pattern")
                    self._heuristic_links["behavioral_pattern"].add(f"{pair[0]}:{pair[1]}")
                    self.uf.union(addrs[i], addrs[j])

        # Build cluster view
        clusters = []
        raw = self.uf.get_all_clusters()
        for root, members in raw.items():
            beh_pairs = [
                1
                for a in members
                for b in members
                if a < b and "behavioral_pattern" in self._pair_heuristics.get(tuple(sorted([a, b])), set())
            ]
            if beh_pairs and len(members) >= 2:
                c = Cluster(f"behavioral_{root[:12]}")
                c.wallets = members
                c.detection_methods = ["behavioral_pattern"]
                c.confidence = min(0.65, 0.35 + len(members) * 0.04)
                c.center_wallet = self._find_center(members)
                clusters.append(c)
        return clusters

    # ── Heuristic 4: Common Funding Source ───────────────────────

    def detect_funding_clusters(self) -> list[Cluster]:
        """Wallets funded from the same source are likely same operator."""
        # Find first incoming tx for each wallet (the funder)
        funders: dict[str, str] = {}
        for addr, profile in self.wallets.items():
            if profile.transaction_times:
                min(profile.transaction_times)
                # The counterparty at the earliest tx is the funder
                for cp in profile.unique_counterparties:
                    funders[addr] = cp
                    break

        # Group by funder
        funder_groups: dict[str, set[str]] = defaultdict(set)
        for wallet, funder in funders.items():
            funder_groups[funder].add(wallet)

        clusters = []
        for funder, wallets in funder_groups.items():
            if len(wallets) < 2:
                continue
            # Link all wallets funded by same source
            wl = list(wallets)
            for i in range(len(wl)):
                for j in range(i + 1, len(wl)):
                    pair = tuple(sorted([wl[i], wl[j]]))
                    self._pair_heuristics[pair].add("common_funding")
                    self._heuristic_links["common_funding"].add(f"{pair[0]}:{pair[1]}")
                    self.uf.union(wl[i], wl[j])

            c = Cluster(f"funding_{funder[:12]}")
            c.wallets = wallets
            c.detection_methods = ["common_funding"]
            c.confidence = 0.80 if len(wallets) >= 5 else 0.60
            c.center_wallet = funder
            clusters.append(c)

        return clusters

    # ── Heuristic 5: Gas Payer Linkage (NEW — for deployer networks) ─

    def detect_gas_payer_clusters(self, gas_payer_map: dict[str, list[str]] | None = None) -> list[Cluster]:
        """
        Link wallets where one address pays gas for multiple contract
        deployments or token transfers. Primary heuristic for identifying
        serial deployer networks.

        Args:
            gas_payer_map: Optional external data {gas_payer: [recipients]}.
                          If not provided, inferred from transaction data.
        """
        if gas_payer_map is None:
            # Infer from existing data: from_address paid for to_address
            gas_payer_map = defaultdict(set)
            for addr, profile in self.wallets.items():
                for cp in profile.unique_counterparties:
                    gas_payer_map[addr].add(cp)

        clusters = []
        for payer, recipients in gas_payer_map.items():
            recipients_set = {r.lower() for r in recipients}
            payer_lower = payer.lower()
            recipients_set.discard(payer_lower)  # Don't link to self

            if len(recipients_set) < self.MIN_GAS_PAYER_RECIPIENTS:
                continue

            # Link payer to all recipients
            self.uf.find(payer_lower)
            for r in recipients_set:
                self.uf.find(r)
                pair = tuple(sorted([payer_lower, r]))
                self._pair_heuristics[pair].add("gas_payer")
                self._heuristic_links["gas_payer"].add(f"{pair[0]}:{pair[1]}")
                self.uf.union(payer_lower, r)

            c = Cluster(f"gas_payer_{payer_lower[:12]}")
            c.wallets = recipients_set | {payer_lower}
            c.detection_methods = ["gas_payer"]
            c.confidence = 0.85
            c.center_wallet = payer_lower
            clusters.append(c)

        return clusters

    # ── Heuristic 6: Deposit-Withdraw Pattern (NEW — CEX tracing) ─

    def detect_deposit_withdraw_clusters(
        self,
        exchange_deposit_map: dict[str, list[str]] | None = None,
        exchange_withdrawal_map: dict[str, list[str]] | None = None,
    ) -> list[Cluster]:
        """
        When funds flow: wallet_A → exchange deposit → exchange hot wallet →
        exchange withdrawal → wallet_B, link wallet_A and wallet_B to same entity.

        Args:
            exchange_deposit_map: {exchange_hot_wallet: [depositing_addresses]}
            exchange_withdrawal_map: {exchange_hot_wallet: [withdrawal_addresses]}
        """
        if not exchange_deposit_map or not exchange_withdrawal_map:
            # Without external CEX data, we can't do this heuristic
            return []

        clusters = []
        for hot_wallet in set(exchange_deposit_map.keys()) & set(exchange_withdrawal_map.keys()):
            depositors = {a.lower() for a in exchange_deposit_map[hot_wallet]}
            withdrawers = {a.lower() for a in exchange_withdrawal_map[hot_wallet]}

            if len(depositors) < 2 or len(withdrawers) < 2:
                continue

            # Link all depositors and withdrawers that share the same hot wallet
            all_addrs = depositors | withdrawers
            al = list(all_addrs)
            for i in range(len(al)):
                for j in range(i + 1, len(al)):
                    pair = tuple(sorted([al[i], al[j]]))
                    self._pair_heuristics[pair].add("deposit_withdraw")
                    self._heuristic_links["deposit_withdraw"].add(f"{pair[0]}:{pair[1]}")
                    self.uf.union(al[i], al[j])

            c = Cluster(f"cex_flow_{hot_wallet[:12]}")
            c.wallets = all_addrs
            c.detection_methods = ["deposit_withdraw"]
            c.confidence = 0.80
            c.center_wallet = hot_wallet.lower()
            clusters.append(c)

        return clusters

    # ── Run all heuristics ────────────────────────────────────────

    def run_all_heuristics(
        self,
        gas_payer_map: dict[str, list[str]] | None = None,
        exchange_deposit_map: dict[str, list[str]] | None = None,
        exchange_withdrawal_map: dict[str, list[str]] | None = None,
    ) -> list[Cluster]:
        """Run all 6 heuristics and return merged clusters."""
        all_clusters = []

        all_clusters.extend(self.detect_temporal_clusters())
        all_clusters.extend(self.detect_counterparty_clusters())
        all_clusters.extend(self.detect_behavioral_clusters())
        all_clusters.extend(self.detect_funding_clusters())
        all_clusters.extend(self.detect_gas_payer_clusters(gas_payer_map))
        all_clusters.extend(self.detect_deposit_withdraw_clusters(exchange_deposit_map, exchange_withdrawal_map))

        # The Union-Find already merged everything — now extract the final clusters
        final = self._build_final_clusters()
        return final

    def _build_final_clusters(self) -> list[Cluster]:
        """Extract final merged clusters from Union-Find with accumulated heuristics."""
        raw = self.uf.get_all_clusters()
        clusters = []

        for root, members in raw.items():
            if len(members) < 2:
                continue

            # Collect all heuristics that contributed to this cluster
            all_heuristics: set[str] = set()
            members_list = list(members)
            for i in range(len(members_list)):
                for j in range(i + 1, len(members_list)):
                    pair = tuple(sorted([members_list[i], members_list[j]]))
                    all_heuristics |= self._pair_heuristics.get(pair, set())

            # Confidence = weighted sum of contributing heuristics
            total_weight = sum(HEURISTIC_WEIGHTS.get(h, 0.5) for h in all_heuristics)
            # Normalize: max confidence when all 6 heuristics contribute
            max_possible = sum(HEURISTIC_WEIGHTS.values())
            confidence = min(0.99, total_weight / max_possible) if max_possible else 0.5

            c = Cluster(f"entity_{root[:16]}")
            c.wallets = members
            c.confidence = round(confidence, 3)
            c.detection_methods = sorted(all_heuristics)
            c.center_wallet = self._find_center(members)
            clusters.append(c)

        return clusters

    # ── Helpers ───────────────────────────────────────────────────

    def _fingerprint_similarity(self, fp1: dict, fp2: dict) -> float:
        scores = []
        if fp1["avg_size"] > 0 and fp2["avg_size"] > 0:
            scores.append(min(fp1["avg_size"], fp2["avg_size"]) / max(fp1["avg_size"], fp2["avg_size"]))
        if fp1["frequency"] > 0 and fp2["frequency"] > 0:
            scores.append(min(fp1["frequency"], fp2["frequency"]) / max(fp1["frequency"], fp2["frequency"]))
        h1, h2 = fp1["preferred_hours"], fp2["preferred_hours"]
        if h1 and h2:
            scores.append(len(h1 & h2) / len(h1 | h2))
        if fp1["program_diversity"] > 0 and fp2["program_diversity"] > 0:
            scores.append(
                min(fp1["program_diversity"], fp2["program_diversity"])
                / max(fp1["program_diversity"], fp2["program_diversity"])
            )
        return sum(scores) / len(scores) if scores else 0

    def _find_center(self, wallets: set[str]) -> str | None:
        if not wallets:
            return None
        best, best_count = None, 0
        for w in wallets:
            if w in self.wallets:
                count = len(self.wallets[w].unique_counterparties & wallets)
                if count > best_count:
                    best, best_count = w, count
        return best or next(iter(wallets))

    def get_cluster_for_wallet(self, address: str) -> Cluster | None:
        """Get the cluster containing a specific wallet."""
        root = self.uf.find(address.lower())
        members = self.uf.get_members(address.lower())
        if len(members) < 2:
            return None
        c = Cluster(f"entity_{root[:16]}")
        c.wallets = members
        c.center_wallet = root
        return c

    def get_stats(self) -> dict:
        """Return engine statistics."""
        clusters = self.uf.get_all_clusters()
        multi = {k: v for k, v in clusters.items() if len(v) >= 2}
        return {
            "total_wallets": self.uf.total_nodes,
            "total_clusters": len(multi),
            "total_singletons": len(clusters) - len(multi),
            "largest_cluster": max((len(v) for v in multi.values()), default=0),
            "heuristic_links": {k: len(v) for k, v in self._heuristic_links.items()},
        }
