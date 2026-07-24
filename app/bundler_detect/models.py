"""
Bundler Detector - Data Models
===============================
Data models, enums, and constants for supply manipulation detection.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.shared_utils import EVM_ADDRESS_RE, SOLANA_ADDRESS_RE

# ── Constants ──────────────────────────────────────────────────

EVM_CHAINS = frozenset({
    "ethereum", "bsc", "polygon", "arbitrum", "optimism",
    "avalanche", "base", "fantom", "linea", "zksync",
    "scroll", "mantle",
})

SUPPORTED_CHAINS = [*EVM_CHAINS, "solana"]

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"
BIRDEYE_PUBLIC = "https://public-api.birdeye.so"
KNOWN_BUNDLER_SEEDS: set[str] = set()

# ── Risk Levels ────────────────────────────────────────────────

class BundlerRisk(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"

# ── Data Models ────────────────────────────────────────────────

@dataclass
class BundledBuy:
    """A single suspicious buy event identified as potentially bundled."""

    wallet: str
    amount_usd: float
    buy_block: int
    buy_timestamp: float
    tx_hash: str = ""
    funding_source: str = ""
    is_sniper: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet": self.wallet,
            "amount_usd": round(self.amount_usd, 2),
            "buy_block": self.buy_block,
            "buy_timestamp": self.buy_timestamp,
            "tx_hash": self.tx_hash,
            "funding_source": self.funding_source,
            "is_sniper": self.is_sniper,
        }

@dataclass
class HolderCluster:
    """A cluster of wallets suspected to be controlled by one entity."""

    wallets: list[str]
    total_supply_pct: float
    funding_overlap_score: float  # 0-1
    buy_time_similarity: float  # 0-1
    common_funding_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_count": len(self.wallets),
            "wallets": self.wallets[:20],
            "total_supply_pct": round(self.total_supply_pct, 2),
            "funding_overlap_score": round(self.funding_overlap_score, 3),
            "buy_time_similarity": round(self.buy_time_similarity, 3),
            "common_funding_source": self.common_funding_source,
        }

@dataclass
class BundlerReport:
    """Full supply manipulation analysis result."""

    token_address: str
    chain: str
    name: str = ""
    symbol: str = ""

    # Core scores (0-100)
    bundler_score: float = 0.0
    supply_concentration_score: float = 0.0
    sniper_cluster_score: float = 0.0
    launch_timing_anomaly_score: float = 0.0
    fund_flow_risk_score: float = 0.0

    # Findings
    suspected_bundled_buys: list[BundledBuy] = field(default_factory=list)
    holder_clusters: list[HolderCluster] = field(default_factory=list)
    top_10_holder_concentration: float = 0.0
    dev_hold_pct: float = 0.0
    unique_buyers_first_block: int = 0
    total_buys_first_blocks: int = 0
    buys_from_same_funding: int = 0
    estimated_unique_entities: int = 0

    risk_label: str = "none"
    errors: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_address": self.token_address,
            "chain": self.chain,
            "name": self.name,
            "symbol": self.symbol,
            "bundler_score": round(self.bundler_score, 1),
            "risk_label": self.risk_label,
            "signals": {
                "supply_concentration": round(self.supply_concentration_score, 1),
                "sniper_cluster": round(self.sniper_cluster_score, 1),
                "launch_timing_anomaly": round(self.launch_timing_anomaly_score, 1),
                "fund_flow_risk": round(self.fund_flow_risk_score, 1),
            },
            "suspected_bundled_buys": [b.to_dict() for b in self.suspected_bundled_buys[:50]],
            "holder_clusters": [c.to_dict() for c in self.holder_clusters[:10]],
            "top_10_holder_concentration": round(self.top_10_holder_concentration, 2),
            "dev_hold_pct": round(self.dev_hold_pct, 2),
            "unique_buyers_first_block": self.unique_buyers_first_block,
            "total_buys_first_blocks": self.total_buys_first_blocks,
            "buys_from_same_funding": self.buys_from_same_funding,
            "estimated_unique_entities": self.estimated_unique_entities,
        }

    def summary(self) -> str:
        flags = []
        if self.top_10_holder_concentration > 80:
            flags.append(f"top10hld:{self.top_10_holder_concentration:.0f}%")
        if self.buys_from_same_funding > 3:
            flags.append(f"shared_fund:{self.buys_from_same_funding}x")
        if self.suspected_bundled_buys:
            flags.append(f"bundled:{len(self.suspected_bundled_buys)}buys")
        if self.holder_clusters:
            total_cluster_pct = sum(c.total_supply_pct for c in self.holder_clusters)
            flags.append(f"clustered:{total_cluster_pct:.0f}%")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        return (
            f"[{self.risk_label.upper()}] {self.token_address[:14]}... "
            f"({self.name}/{self.symbol}) - "
            f"Bundler score: {self.bundler_score:.0f}/100 | "
            f"{len(self.holder_clusters)} clusters | "
            f"{self.estimated_unique_entities} entities estimated"
            f"{flag_str}"
        )

# ── Scoring Weights ────────────────────────────────────────────

BUNDLER_SCORE_WEIGHTS: dict[str, float] = {
    "supply_concentration": 0.30,
    "sniper_cluster": 0.25,
    "launch_timing_anomaly": 0.20,
    "fund_flow_risk": 0.25,
}
