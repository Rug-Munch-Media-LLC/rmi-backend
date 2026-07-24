"""
Degen Security Scanner — Data Models
======================================
Dataclasses for token security analysis reports.
"""

import json
from dataclasses import asdict, dataclass, field


@dataclass
class HolderProfile:
    """Detailed holder analysis."""

    address: str
    balance: float
    percentage: float
    exchange_tag: str | None = None
    is_contract: bool = False
    is_known_whale: bool = False
    first_seen: str | None = None
    funding_source: str | None = None
    smart_money_tag: str | None = None
    is_sniper: bool = False
    is_insider: bool = False
    is_bundler: bool = False
    is_wash_trader: bool = False
    other_tokens_held: int = 0
    risk_score: int = 0


@dataclass
class FundingTrace:
    """Gas funding trace result."""

    wallet: str
    initial_funder: str
    funder_tag: str | None = None
    funding_amount_sol: float = 0.0
    funding_time: str | None = None
    hops_from_exchange: int = -1  # -1 = unknown, 0 = direct exchange, 1+ = hops
    is_exchange_direct: bool = False
    exchange_name: str | None = None
    risk_flags: list[str] = field(default_factory=list)


@dataclass
class SniperProfile:
    """Launch sniper analysis."""

    wallet: str
    buy_time_ms: float  # milliseconds after launch
    buy_amount_sol: float
    token_amount: float
    price_paid: float
    current_value: float = 0.0
    pnl_percent: float = 0.0
    is_still_holding: bool = True
    sold_percentage: float = 0.0
    is_known_sniper: bool = False
    sniper_score: int = 0  # 0-100


@dataclass
class LPAnalysis:
    """Liquidity pool analysis."""

    pool_address: str
    dex_name: str
    token_a_amount: float
    token_b_amount: float
    tvl_usd: float
    lp_tokens_total: float
    lp_tokens_locked: float
    lp_locked_percentage: float = 0.0
    lock_expiry: str | None = None
    is_burned: bool = False
    burn_tx: str | None = None
    lp_holders: list[dict] = field(default_factory=list)
    concentration_risk: str = "LOW"  # LOW, MEDIUM, HIGH, CRITICAL
    depth_score: int = 0  # 0-100


@dataclass
class WashTradeCluster:
    """Detected wash trading cluster."""

    wallets: list[str]
    trade_count: int
    volume_usd: float
    pattern_type: str  # circular, back_forth, triangular
    confidence: float  # 0.0 - 1.0
    sample_txs: list[str] = field(default_factory=list)


@dataclass
class SecurityReport:
    """Final comprehensive security report."""

    # Token Identity
    token_address: str
    token_name: str = "Unknown"
    token_symbol: str = "???"
    decimals: int = 0
    total_supply: float = 0.0
    created_at: str | None = None
    deployer: str = ""

    # Authority Status
    mint_authority: str | None = None
    freeze_authority: str | None = None
    mint_authority_renounced: bool = False
    freeze_authority_renounced: bool = False
    authorities_risk: str = "LOW"

    # Holder Analysis
    total_holders: int = 0
    top_holders: list[HolderProfile] = field(default_factory=list)
    top_10_concentration: float = 0.0
    top_20_concentration: float = 0.0
    top_50_concentration: float = 0.0
    holder_health_score: int = 0

    # Funding & Origins
    deployer_funding: FundingTrace | None = None
    exchange_funding_percentage: float = 0.0
    organic_funding_percentage: float = 0.0
    mixer_funding_detected: bool = False
    funding_risk: str = "LOW"

    # Bundler Detection
    bundler_detected: bool = False
    bundler_wallets: list[str] = field(default_factory=list)
    bundler_buy_volume_sol: float = 0.0
    bundler_percentage_of_supply: float = 0.0
    bundler_pattern: str = ""
    bundler_risk: str = "LOW"

    # Sniper Analysis
    snipers: list[SniperProfile] = field(default_factory=list)
    sniper_count: int = 0
    sniper_volume_sol: float = 0.0
    avg_sniper_entry_time_ms: float = 0.0
    sniper_risk: str = "LOW"

    # LP Analysis
    liquidity_pools: list[LPAnalysis] = field(default_factory=list)
    total_liquidity_usd: float = 0.0
    lp_risk: str = "LOW"

    # Insider Trading
    insiders_detected: list[str] = field(default_factory=list)
    insider_buy_volume: float = 0.0
    insider_risk: str = "LOW"

    # Wash Trading
    wash_trade_clusters: list[WashTradeCluster] = field(default_factory=list)
    wash_trading_volume_usd: float = 0.0
    wash_trading_risk: str = "LOW"

    # Tax & Limits
    buy_tax: float = 0.0
    sell_tax: float = 0.0
    transfer_tax: float = 0.0
    max_buy_limit: float = 0.0
    max_sell_limit: float = 0.0
    max_wallet_limit: float = 0.0
    honeypot_detected: bool = False
    can_sell: bool = True
    tax_risk: str = "LOW"

    # Market Data
    price_usd: float = 0.0
    market_cap: float = 0.0
    volume_24h: float = 0.0
    price_change_24h: float = 0.0
    liquidity_usd: float = 0.0
    buy_sell_ratio: float = 1.0

    # Smart Money
    smart_money_holders: int = 0
    dumb_money_holders: int = 0
    smart_money_ratio: float = 0.0

    # Dev History
    dev_other_tokens: list[str] = field(default_factory=list)
    dev_rug_history: int = 0
    dev_is_known_scammer: bool = False
    dev_risk: str = "LOW"

    # Final Scores
    overall_risk: str = "LOW"
    rug_pull_probability: int = 0
    confidence_level: str = "HIGH"
    critical_warnings: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    scan_timestamp: str = ""
    scan_duration_ms: int = 0

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        result = {}
        for k, v in asdict(self).items():
            if isinstance(v, list) and len(v) > 0 and hasattr(v[0], "to_dict"):
                result[k] = [item.to_dict() if hasattr(item, "to_dict") else item for item in v]
            elif hasattr(v, "to_dict"):
                result[k] = v.to_dict()
            else:
                result[k] = v
        return result

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


__all__ = [
    "FundingTrace",
    "HolderProfile",
    "LPAnalysis",
    "SecurityReport",
    "SniperProfile",
    "WashTradeCluster",
]
