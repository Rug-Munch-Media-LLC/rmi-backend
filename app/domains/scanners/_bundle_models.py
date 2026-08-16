"""Bundle-detection data models & constants (pure, no I/O).

Extracted from ``bundle_detector.py`` during the de-godding round 5 pass:
the dataclasses describing detected bundle/sniper groups plus the
program-ID / explorer constant tables. ``bundle_detector`` re-exports every
name so all importers and tests keep working against the same objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FundingStep:
    address: str
    amount: float
    token: str  # "SOL", "ETH", etc.
    timestamp: int  # unix ms
    tx_signature: str = ""


@dataclass
class BundledWallet:
    address: str
    funding_chain: list[FundingStep]
    buy_tx: str | None = None
    buy_amount: float = 0.0
    buy_block: int = 0
    confidence: str = "MEDIUM"  # LOW, MEDIUM, HIGH, VERY_HIGH
    evidence: list[str] = field(default_factory=list)


@dataclass
class SameBlockSniper:
    address: str
    block_number: int
    buy_amount: float = 0.0
    compute_units: int = 0  # Solana-specific
    priority_fee: float = 0.0
    is_jito: bool = False


@dataclass
class BehaviorCluster:
    wallets: list[str]
    pattern: str  # "same_size", "same_time", "same_dex", "mixed"
    combined_percentage: float
    confidence: float


@dataclass
class BundleReport:
    token_address: str
    chain: str
    deployer: str
    deploy_block: int
    deploy_timestamp: int
    total_bundled_percentage: float
    deployer_funded: list[BundledWallet]
    same_block_snipers: list[SameBlockSniper]
    behavior_clusters: list[BehaviorCluster]
    compute_budget_patterns: dict[str, int] = field(default_factory=dict)
    jito_bundles_detected: int = 0
    risk_score: int = 0  # 0-100
    risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    graph_fraud_score: int = 0  # 0-100, from graph-based fraud pattern analysis
    graph_fraud_report: dict[str, object] | None = None
    warnings: list[str] = field(default_factory=list)
    cross_launch_flags: dict[str, Any] = field(default_factory=dict)
    # Same-slot buy concentration (own_bundle_detect.detect_same_slot_bundler —
    # Helius-backed on Solana; ``[REVIEW]`` note when the data source is
    # unreachable). Kept separate from same_block_snipers so the deep report
    # can render it as its own line.
    same_slot: dict[str, Any] | None = None


@dataclass
class _EarlyBuyer:
    address: str
    first_buy_tx: str = ""
    first_buy_block: int = 0
    buy_amount: float = 0.0
    compute_units: int = 0
    priority_fee: float = 0.0
    is_jito: bool = False
    dex_route: str = ""


# Known Jito tip program IDs
JITO_PROGRAMS: frozenset[str] = frozenset(
    {
        "Jito4APyf6rDt1pD1jH3nD3is4v7TwzFNWjjMP7B2RK",
        "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
        "ADuCadRmgjMe6UzXVxR1Kn9CS2CXAf8bjiKkC4xFcegX",
    }
)

# Known bundler program IDs (Smithii, Printr, etc.)
BUNDLER_PROGRAMS: dict[str, str] = {
    "Smithii": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "PumpFun": "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",
    "LaunchLab": "LaunchLabzbP1j4R7KzEFmYSF3EXGnZ6tY7AZ8eg3P2qJ",
}

# Etherscan-family block explorer API URLs (fallback if chain registry unset)
EVM_EXPLORER_URLS: dict[str, str] = {
    "ethereum": "https://api.etherscan.io/api",
    "base": "https://api.basescan.org/api",
    "bsc": "https://api.bscscan.com/api",
}

# Env vars holding each chain's explorer API key (fallback if registry unset)
EVM_EXPLORER_KEY_ENVS: dict[str, str] = {
    "ethereum": "ETHERSCAN_API_KEY",
    "base": "BASESCAN_API_KEY",
    "bsc": "BSCSCAN_API_KEY",
}
