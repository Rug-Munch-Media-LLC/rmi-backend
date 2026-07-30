"""ScanResult dataclass — data model for token scan results."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScanResult:
    """Single unified scan result."""

    token_address: str
    chain: str
    scanned_at: str = ""

    # Core identity
    name: str = ""
    symbol: str = ""
    price_usd: float = 0.0
    market_cap_usd: float = 0.0
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    age_hours: float = 0.0

    # Security
    safety_score: float = 50.0  # 0=scam, 100=safe
    confidence: float = 0.0  # 0-100, how much data we had
    risk_level: str = "unknown"
    risk_flags: list[str] = field(default_factory=list)
    green_flags: list[str] = field(default_factory=list)

    # Enrichment results (keyed by DataBus chain name)
    security: dict | None = None
    holders: dict | None = None
    holder_health: dict | None = None
    liquidity: dict | None = None
    volume_auth: dict | None = None
    dev_reputation: dict | None = None
    rug_patterns: dict | None = None
    premium: dict | None = None  # bundle/cluster/sniper/mev/wash/copy/insider
    smart_money: dict | None = None
    whale_alerts: dict | None = None
    cross_chain: dict | None = None
    launches: dict | None = None
    chain_indexer_patterns: dict | None = None

    # Metadata
    modules_run: list[str] = field(default_factory=list)
    enrichment_sources: list[str] = field(default_factory=list)
    tier: str = "free"
    scan_duration_ms: float = 0.0
