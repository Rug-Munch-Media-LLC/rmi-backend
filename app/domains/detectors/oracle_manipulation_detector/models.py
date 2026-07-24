"""
Oracle Manipulation Detector — Data Models
============================================
Enums and dataclasses for oracle manipulation detection.
"""

from __future__ import annotations
import json
from dataclasses import asdict, dataclass, field
from enum import Enum

class OracleType(Enum):
    """Supported oracle types."""
    CHAINLINK = "chainlink"
    UNISWAP_V2_TWAP = "uniswap_v2_twap"
    UNISWAP_V3_TWAP = "uniswap_v3_twap"
    BALANCER_TWAP = "balancer_twap"
    CURVE_EMA = "curve_ema"
    MAKER_OSM = "maker_osm"
    PUSH_ORACLE = "push_oracle"
    CUSTOM = "custom"

    @classmethod
    def from_string(cls, s: str) -> "OracleType":
        for member in cls:
            if member.value == s.lower():
                return member
        return cls.CUSTOM

class ManipulationType(Enum):
    """Types of oracle manipulation attacks."""
    TWAP_POISONING = "twap_poisoning"
    CHAINLINK_STALE = "chainlink_stale"
    FLASH_LOAN_SWAP = "flash_loan_swap"
    LP_PRICE_DIVERGENCE = "lp_price_divergence"
    CEX_DEX_DIVERGENCE = "cex_dex_divergence"
    SANDWICH_PRICE_IMPACT = "sandwich_price_impact"
    LENDING_ORACLE_EXPLOIT = "lending_oracle_exploit"
    FRESH_WALLET_SWAP = "fresh_wallet_swap"
    PROPORTIONAL_VOLUME_ANOMALY = "proportional_volume_anomaly"

class Severity(Enum):
    """Severity levels for detected incidents."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"

    @property
    def score(self) -> float:
        return {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25, "info": 0.0}[self.value]

    def __lt__(self, other) -> bool:
        return self.score < other.score if isinstance(other, Severity) else NotImplemented

@dataclass
class PriceSnapshot:
    """A single price observation at a point in time."""
    timestamp: float
    block_number: int
    price: float
    liquidity: float = 0.0
    source: str = ""
    chain: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class TWAPWindow:
    """Time-Weighted Average Price window data."""
    start_block: int
    end_block: int
    start_time: float
    end_time: float
    average_price: float
    median_price: float
    min_price: float
    max_price: float
    std_dev_pct: float
    samples: list[PriceSnapshot]
    oracle_type: OracleType = OracleType.CUSTOM
    pool_address: str = ""
    pair: str = ""

    def manipulation_risk(self) -> float:
        """Calculate manipulation risk score (0.0 - 1.0)."""
        if self.std_dev_pct <= 0.001:
            return 0.0
        risk = min(self.std_dev_pct / 0.10, 1.0)
        return risk

    def to_dict(self) -> dict:
        result = asdict(self)
        result["oracle_type"] = self.oracle_type.value
        result["samples"] = [s.to_dict() for s in self.samples]
        result["manipulation_risk"] = self.manipulation_risk()
        return result

@dataclass
class OracleRead:
    """A single oracle price read with metadata."""
    tx_hash: str
    block_number: int
    timestamp: float
    oracle_address: str
    oracle_type: OracleType
    reported_price: float
    expected_price: float | None = None
    price_age_seconds: float = 0.0
    chain: str = "ethereum"
    protocol: str = ""

    def is_stale(self) -> bool:
        from .constants import CHAINLINK_FEED_UPDATE_THRESHOLD_SECONDS
        return self.price_age_seconds > CHAINLINK_FEED_UPDATE_THRESHOLD_SECONDS

    def deviation_from_expected(self) -> float | None:
        if self.expected_price and self.expected_price > 0:
            return abs(self.reported_price - self.expected_price) / self.expected_price
        return None

    def to_dict(self) -> dict:
        result = asdict(self)
        result["oracle_type"] = self.oracle_type.value
        result["is_stale"] = self.is_stale()
        return result

@dataclass
class PriceManipulation:
    """A single detected price manipulation incident."""
    manipulation_type: ManipulationType
    severity: Severity
    chain: str
    block_number: int
    pool_address: str
    pair: str
    oracle_type: OracleType
    observed_price: float
    expected_price: float
    deviation_pct: float
    description: str = ""
    evidence: list[str] = field(default_factory=list)
    tx_hash: str = ""
    timestamp: float = 0.0
    flash_loan_value_usd: float = 0.0
    related_txns: list[str] = field(default_factory=list)

    def label(self) -> str:
        return f"[{self.severity.value.upper()}] {self.manipulation_type.value} on {self.pair}"

    def summary(self) -> str:
        return (
            f"{self.label()}: {self.description} "
            f"(deviation={self.deviation_pct:.2f}%, pool={self.pool_address[:10]}...)"
        )

    def to_dict(self) -> dict:
        result = asdict(self)
        result["manipulation_type"] = self.manipulation_type.value
        result["severity"] = self.severity.value
        result["oracle_type"] = self.oracle_type.value
        result["label"] = self.label()
        return result

@dataclass
class ManipulationReport:
    """Complete oracle manipulation scan report."""
    scan_id: str
    chain: str
    blocks_scanned: int
    start_time: float
    end_time: float
    incidents: list[PriceManipulation] = field(default_factory=list)
    twap_windows: list[TWAPWindow] = field(default_factory=list)
    oracle_reads: list[OracleRead] = field(default_factory=list)
    pools_checked: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        return self.end_time - self.start_time

    @property
    def critical_count(self) -> int:
        return sum(1 for inc in self.incidents if inc.severity == Severity.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for inc in self.incidents if inc.severity == Severity.HIGH)

    @property
    def total_incidents(self) -> int:
        return len(self.incidents)

    @property
    def risk_score(self) -> float:
        if not self.incidents:
            return 0.0
        return sum(inc.severity.score * (1 + inc.deviation_pct / 100) for inc in self.incidents) / len(self.incidents)

    def to_dict(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "chain": self.chain,
            "blocks_scanned": self.blocks_scanned,
            "duration_seconds": self.duration_seconds,
            "pools_checked": self.pools_checked,
            "total_incidents": self.total_incidents,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "risk_score": self.risk_score,
            "incidents": [i.to_dict() for i in self.incidents],
            "twap_windows": [t.to_dict() for t in self.twap_windows],
            "oracle_reads": [r.to_dict() for r in self.oracle_reads],
            "errors": self.errors,
        }

    def json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

__all__ = [
    "ManipulationReport",
    "ManipulationType",
    "OracleRead",
    "OracleType",
    "PriceManipulation",
    "PriceSnapshot",
    "Severity",
    "TWAPWindow",
]
