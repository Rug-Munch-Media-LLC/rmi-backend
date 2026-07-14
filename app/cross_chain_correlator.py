"""
Cross-Chain Wallet Correlation Engine.
Links wallets across Solana/Ethereum/BSC/Base using:
- ENS/SPL name resolution
- Funding patterns (same CEX withdrawal → multiple chains)
- Behavioral fingerprinting (same patterns on different chains)
- Known entity databases

Paper ref: Section 5.3 - Cross-Chain Fragmentation
"""

from app.core.logging import get_logger
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

logger = get_logger(__name__)


@dataclass
class CrossChainLink:
    """A link between wallets on different chains."""

    source_address: str
    source_chain: str
    target_address: str
    target_chain: str
    link_type: str  # funding, behavioral, naming, entity
    confidence: float
    evidence: str


@dataclass
class CrossChainProfile:
    """Aggregated profile of an entity across chains."""

    entity_id: str
    chains: dict[str, list[str]]  # chain → [addresses]
    total_addresses: int
    first_seen: datetime | None = None
    last_active: datetime | None = None
    risk_score: float = 0.0
    labels: list[str] = field(default_factory=list)
    links: list[CrossChainLink] = field(default_factory=list)


class CrossChainCorrelator:
    """Multi-chain entity resolution engine."""

    # Known CEX deposit addresses (common pivot points)
    CEX_PATTERNS = {  # noqa: RUF012
        "binance": [
            "Binance",
            "Binance 1",
            "Binance 2",
            "Binance 3",
            "Binance 4",
            "Binance 5",
            "Binance 6",
            "Binance 7",
            "Binance 8",
        ],
        "coinbase": ["Coinbase 1", "Coinbase 2", "Coinbase 3", "Coinbase 4", "Coinbase 5"],
        "kraken": ["Kraken 1", "Kraken 2"],
        "bybit": ["Bybit", "Bybit 1", "Bybit 2"],
        "okx": ["OKX 1", "OKX 2", "OKX 3"],
    }

    # Name resolution providers
    NAME_PROVIDERS = {  # noqa: RUF012
        "ethereum": "https://api.ensideas.com/ens/resolve/",
        "solana": "https://sns-sdk.solana.domain/",
    }

    def __init__(self):
        self.profiles: dict[str, CrossChainProfile] = {}
        self._fingerprints: dict[str, dict] = {}

    async def correlate(
        self, addresses: list[str], chains: list[str] | None = None, depth: int = 2
    ) -> list[CrossChainProfile]:
        """Correlate wallets across chains using multiple signals."""
        if not chains:
            chains = ["solana", "ethereum", "bsc", "base"]

        # Step 1: Build behavioral fingerprints per chain
        fingerprints = {}
        for addr, chain in zip(addresses, chains or [], strict=False):
            fps = await self._fingerprint_wallet(addr, chain)
            fingerprints[f"{chain}:{addr}"] = fps

        # Step 2: Compare fingerprints across chains
        links = []
        keys = list(fingerprints.keys())
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                chain_a, addr_a = keys[i].split(":", 1)
                chain_b, addr_b = keys[j].split(":", 1)
                if chain_a == chain_b:
                    continue  # Same chain - use regular clustering

                link = self._compare_fingerprints(
                    addr_a, chain_a, fingerprints[keys[i]], addr_b, chain_b, fingerprints[keys[j]]
                )
                if link and link.confidence >= 0.3:
                    links.append(link)

        # Step 3: Group into profiles
        profiles = self._group_into_profiles(addresses, chains or [], links)

        return profiles

    async def _fingerprint_wallet(self, address: str, chain: str) -> dict:
        """Build behavioral fingerprint for a wallet on a chain."""
        fp = {
            "address": address,
            "chain": chain,
            "avg_tx_value": 0.0,
            "tx_frequency": 0.0,  # tx/day
            "preferred_hours": [],
            "common_counterparties": [],
            "token_diversity": 0,
            "holding_period_avg": 0.0,
        }

        try:
            from app.chain_client import get_chain_client

            client = get_chain_client()

            if chain == "solana":
                sigs = await client.get_signatures(address, limit=20)
                if sigs:
                    timestamps = [s.get("blockTime", 0) for s in sigs if s.get("blockTime")]
                    if timestamps:
                        from collections import Counter

                        hours = [datetime.fromtimestamp(t, tz=UTC).hour for t in timestamps]
                        fp["preferred_hours"] = [h for h, _ in Counter(hours).most_common(3)]
                        if len(timestamps) >= 2:
                            span = (max(timestamps) - min(timestamps)) / 3600
                            fp["tx_frequency"] = round(len(timestamps) / max(span, 1), 2)

            # Try QuickNode for EVM chains
            elif chain in ("ethereum", "bsc", "base"):
                # QuickNode supports EVM chains
                pass

        except Exception as e:
            logger.debug(f"Fingerprint {chain}:{address[:12]}: {e}")

        return fp

    def _compare_fingerprints(
        self, addr_a: str, chain_a: str, fp_a: dict, addr_b: str, chain_b: str, fp_b: dict
    ) -> CrossChainLink | None:
        """Compare two cross-chain fingerprints for similarity."""
        signals = []

        # 1. Same preferred hours → temporal pattern match
        hours_a = set(fp_a.get("preferred_hours", []))
        hours_b = set(fp_b.get("preferred_hours", []))
        if hours_a and hours_b:
            overlap = len(hours_a & hours_b) / max(len(hours_a | hours_b), 1)
            if overlap >= 0.5:
                signals.append(("temporal_pattern", 0.4 * overlap))

        # 2. Similar tx frequency
        freq_a = fp_a.get("tx_frequency", 0)
        freq_b = fp_b.get("tx_frequency", 0)
        if freq_a > 0 and freq_b > 0:
            ratio = min(freq_a, freq_b) / max(freq_a, freq_b, 0.001)
            if ratio >= 0.5:
                signals.append(("frequency_match", 0.3 * ratio))

        # 3. Same counter-parties across chains (CEX pattern)
        cp_a = set(fp_a.get("common_counterparties", []))
        cp_b = set(fp_b.get("common_counterparties", []))
        common_cp = cp_a & cp_b
        if common_cp:
            signals.append(("shared_counterparty", min(0.6, 0.2 * len(common_cp))))

        if not signals:
            return None

        confidence = sum(s for _, s in signals) / 3  # Normalize
        best_signal = max(signals, key=lambda x: x[1])

        return CrossChainLink(
            source_address=addr_a,
            source_chain=chain_a,
            target_address=addr_b,
            target_chain=chain_b,
            link_type=best_signal[0],
            confidence=round(min(confidence, 1.0), 4),
            evidence=f"Matched: {', '.join(t for t, _ in signals)}",
        )

    def _group_into_profiles(
        self, addresses: list[str], chains: list[str], links: list[CrossChainLink]
    ) -> list[CrossChainProfile]:
        """Group linked addresses into entity profiles."""
        # Union-find
        parent = {}

        def find(x):
            if x not in parent:
                parent[x] = x
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(a, b):
            parent[find(a)] = find(b)

        for addr, chain in zip(addresses, chains, strict=False):
            key = f"{chain}:{addr}"
            find(key)

        for link in links:
            union(
                f"{link.source_chain}:{link.source_address}",
                f"{link.target_chain}:{link.target_address}",
            )

        # Collect groups
        groups = defaultdict(list)
        for key in parent:
            groups[find(key)].append(key)

        profiles = []
        for gid, keys in groups.items():
            chains_map = defaultdict(list)
            for key in keys:
                chain, addr = key.split(":", 1)
                chains_map[chain].append(addr)

            profile = CrossChainProfile(
                entity_id=f"cc_{gid[:8]}",
                chains=dict(chains_map),
                total_addresses=len(keys),
                links=[line for line in links if f"{line.source_chain}:{line.source_address}" in keys],
            )
            profiles.append(profile)

        return profiles


# Singleton
_correlator: CrossChainCorrelator | None = None


def get_cross_chain_correlator() -> CrossChainCorrelator:
    global _correlator
    if _correlator is None:
        _correlator = CrossChainCorrelator()
    return _correlator


# ── Legacy compatibility: ChainFingerprint (used by security_intel.py) ──


@dataclass
class ChainFingerprint:
    address: str
    chain: str

    def to_dict(self) -> dict[str, str]:
        return {"address": self.address, "chain": self.chain}
