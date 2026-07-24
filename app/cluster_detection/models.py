"""
Cluster Detection Pro - Data Models
====================================
Shared dataclasses and enums used across the cluster detection modules.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ClusterType(Enum):
    """Types of wallet clusters."""

    BOTNET = "botnet"  # Coordinated bot wallets
    SYBIL = "sybil"  # Same person, multiple wallets
    TEAM = "team"  # Project team wallets
    MARKET_MAKER = "market_maker"  # Market making operation
    WHALE_GROUP = "whale_group"  # Coordinated whales
    SLEEPER = "sleeper"  # Dormant, waiting to activate
    FUNDING = "funding"  # Common funding source
    MIXER = "mixer"  # Tumbler/mixer users
    UNKNOWN = "unknown"


class ClusterConfidence(Enum):
    """Confidence level in cluster detection."""

    CERTAIN = 0.95  # Multiple signals confirm
    HIGH = 0.80  # Strong evidence
    MEDIUM = 0.60  # Moderate evidence
    LOW = 0.40  # Weak evidence
    SUSPECTED = 0.20  # Single indicator


@dataclass
class ClusterSignal:
    """A single clustering signal."""

    signal_type: str
    strength: float  # 0-1
    evidence: dict[str, Any]
    description: str


@dataclass
class WalletCluster:
    """A detected wallet cluster."""

    cluster_id: str
    cluster_type: ClusterType
    confidence: float

    wallets: set[str] = field(default_factory=set)
    center_wallet: str | None = None

    # Detection
    detection_signals: list[ClusterSignal] = field(default_factory=list)
    detection_method: str = ""

    # Temporal
    first_seen: datetime | None = None
    last_active: datetime | None = None
    active_duration_days: int = 0

    # Activity
    total_transactions: int = 0
    total_volume: float = 0.0
    common_tokens: set[str] = field(default_factory=set)
    common_counterparties: set[str] = field(default_factory=set)

    # Behavioral
    avg_transaction_size: float = 0.0
    transaction_frequency: float = 0.0
    preferred_hours: list[int] = field(default_factory=list)

    # Risk
    risk_score: float = 0.0
    associated_scams: list[str] = field(default_factory=list)

    # Evolution
    parent_cluster: str | None = None
    child_clusters: list[str] = field(default_factory=list)
    evolution_history: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "cluster_id": self.cluster_id,
            "type": self.cluster_type.value,
            "confidence": round(self.confidence, 3),
            "wallets": list(self.wallets),
            "wallet_count": len(self.wallets),
            "center_wallet": self.center_wallet,
            "detection": {
                "method": self.detection_method,
                "signals": [
                    {"type": s.signal_type, "strength": s.strength, "description": s.description}
                    for s in self.detection_signals
                ],
            },
            "temporal": {
                "first_seen": self.first_seen.isoformat() if self.first_seen else None,
                "last_active": self.last_active.isoformat() if self.last_active else None,
                "duration_days": self.active_duration_days,
            },
            "activity": {
                "total_transactions": self.total_transactions,
                "total_volume": self.total_volume,
                "common_tokens": list(self.common_tokens),
                "common_counterparties": list(self.common_counterparties),
            },
            "behavioral": {
                "avg_tx_size": self.avg_transaction_size,
                "tx_frequency": self.transaction_frequency,
                "preferred_hours": self.preferred_hours,
            },
            "risk": {"score": self.risk_score, "associated_scams": self.associated_scams},
        }


@dataclass
class FundingPath:
    """A funding path between wallets."""

    source: str
    target: str
    path: list[str]  # Intermediate wallets
    total_amount: float
    transaction_count: int
    first_funding: datetime
    last_funding: datetime
