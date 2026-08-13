"""
Chain config and risk factor data models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ─── CHAIN CONFIG ─────────────────────────────────────────────────

CHAINS = {
    "solana": {"id": "solana", "explorer": "https://solscan.io"},
    "ethereum": {"id": "1", "explorer": "https://etherscan.io"},
    "base": {"id": "8453", "explorer": "https://basescan.org"},
    "bsc": {"id": "56", "explorer": "https://bscscan.com"},
    "arbitrum": {"id": "42161", "explorer": "https://arbiscan.io"},
    "polygon": {"id": "137", "explorer": "https://polygonscan.com"},
    "avalanche": {"id": "43114", "explorer": "https://snowtrace.io"},
    "optimism": {"id": "10", "explorer": "https://optimistic.etherscan.io"},
    "fantom": {"id": "250", "explorer": "https://ftmscan.com"},
    "linea": {"id": "59144", "explorer": "https://lineascan.build"},
    "zksync": {"id": "324", "explorer": "https://explorer.zksync.io"},
    "scroll": {"id": "534352", "explorer": "https://scrollscan.com"},
    "mantle": {"id": "5000", "explorer": "https://mantlescan.xyz"},
}


@dataclass
class WalletRiskFactors:
    """100+ risk factors scored per wallet - populated from real on-chain data."""

    wallet_age_days: int = 0
    first_tx_date: str | None = None
    total_tx_count: int = 0
    tx_frequency_per_day: float = 0
    days_since_last_tx: int = 0
    active_chains: int = 1
    total_volume_usd: float = 0
    avg_tx_value_usd: float = 0
    max_single_tx_usd: float = 0

    funding_source: str | None = None
    funding_source_label: str | None = None
    funding_source_is_cex: bool = False
    funding_source_is_mixer: bool = False
    funding_from_sanctioned: bool = False
    funding_from_scammer: bool = False
    funding_hop_count: int = 0
    cross_chain_funding: bool = False
    tornado_cash_exposure: bool = False
    mixer_interactions: int = 0

    unique_tokens_held: int = 0
    token_launches_participated: int = 0
    sniper_activity_score: float = 0
    sandwich_attacks_executed: int = 0
    flash_loan_usage: int = 0
    rug_pull_launcher_count: int = 0
    honeypot_tokens_owned: int = 0
    tokens_where_top_holder: int = 0

    cex_deposits: int = 0
    cex_withdrawals: int = 0
    dex_swaps: int = 0
    dex_volume_usd: float = 0

    cluster_id: str | None = None
    cluster_size: int = 0
    cluster_shared_funding: bool = False
    known_cluster_type: str = ""
    entity_label: str | None = None
    entity_confidence: float = 0
    is_labeled_scammer: bool = False
    is_labeled_sanctioned: bool = False
    is_labeled_exchange: bool = False
    is_labeled_bot: bool = False
    is_labeled_whale: bool = False
    is_labeled_insider: bool = False
    linked_wallets_count: int = 0

    insider_trading_confidence: float = 0
    buys_before_pumps: int = 0
    sells_before_dumps: int = 0
    wash_trading_score: float = 0
    pump_and_dump_participant: bool = False
    new_token_flipper: bool = False
    round_trip_patterns: int = 0

    scam_token_exposure: int = 0
    phishing_victim: bool = False
    sybil_score: float = 0
    exploit_exposure: int = 0
    protocol_hack_interactions: int = 0
    approval_risk_score: float = 0

    current_balance_usd: float = 0
    portfolio_diversity_score: float = 0
    stablecoin_ratio: float = 0
    bluechip_ratio: float = 0
    memecoin_ratio: float = 0
    realized_pnl_usd: float = 0
    unrealized_pnl_usd: float = 0
    win_rate: float = 0

    held_token_risks: list[dict[str, Any]] = field(default_factory=list)
    highest_risk_token: dict[str, Any] | None = None
    avg_held_token_safety: float = 0

    total_risk_score: int = 0
    risk_category: str = "unknown"
    confidence: float = 0
    data_sources: list[str] = field(default_factory=list)
