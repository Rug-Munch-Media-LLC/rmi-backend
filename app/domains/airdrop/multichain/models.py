"""Multi-chain airdrop data models."""

from __future__ import annotations

from dataclasses import asdict, field
from datetime import UTC, datetime

from app.core.logging import get_logger

logger = get_logger("darkroom_multichain_airdrop")

# ── Data Models ───────────────────────────────────────────────


class TokenSource:
    """A token on a specific chain that qualifies for airdrop."""

    chain: str
    token_address: str
    token_symbol: str
    weight_multiplier: float = 1.0
    min_holdings: str = "0"
    max_holdings: str | None = None
    holding_period_days: int | None = None  # Must hold for N days
    is_nft: bool = False
    nft_collection_id: str | None = None


class CrossChainHolder:
    """Aggregated holder data across multiple chains."""

    # Primary identifier - can be EVM address, Solana pubkey, or linked identity
    primary_address: str
    chain_addresses: dict[str, str] = field(default_factory=dict)
    # { "base": "0x123...", "solana": "ABC...", "ethereum": "0x456..." }

    holdings_per_source: dict[str, str] = field(default_factory=dict)
    # { "base:CRM": "1000000", "solana:CryptoRugMunch": "500000" }

    weighted_score: float = 0.0
    total_tokens_qualified: int = 0
    airdrop_allocation: str = "0"

    # Anti-gaming
    is_contract: bool = False
    is_known_bot: bool = False
    wallet_age_days: int | None = None
    first_seen: str | None = None
    tx_count_30d: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class AirdropTier:
    """Tiered reward structure based on holdings."""

    name: str
    min_score: float
    max_score: float | None = None
    base_allocation: str = "0"
    bonus_multiplier: float = 1.0
    description: str = ""


class MultiChainAirdropCampaign:
    """Full multi-chain airdrop campaign configuration."""

    campaign_id: str
    target_chain: str  # Where airdrop token is deployed
    target_token: str  # Contract address of airdrop token

    # Source qualification criteria
    source_tokens: list[TokenSource] = field(default_factory=list)

    # Allocation rules
    total_airdrop_pool: str = "0"
    distribution_mode: str = "proportional"  # proportional, tiered, fixed
    tiers: list[AirdropTier] = field(default_factory=list)

    # Wiggle room / flexibility
    min_holdings_for_qualification: str = "0"
    max_holdings_cap: str | None = None
    require_hold_both_chains: bool = False  # If True, must hold on ALL specified chains
    require_hold_any_chain: bool = True  # If True, hold on ANY qualifies
    cross_chain_bonus: float = 0.0  # Bonus % for holding on multiple chains

    # Anti-gaming
    exclude_contracts: bool = True
    exclude_known_bots: bool = True
    min_wallet_age_days: int | None = None
    min_tx_count_30d: int | None = None
    snapshot_time_window_hours: int = 24

    # Status
    status: str = "draft"
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    snapshot_completed_at: str | None = None
    distribution_completed_at: str | None = None

    # Results
    qualified_holders: list[CrossChainHolder] = field(default_factory=list)
    total_qualified: int = 0
    total_distributed: str = "0"
    tx_hashes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)
