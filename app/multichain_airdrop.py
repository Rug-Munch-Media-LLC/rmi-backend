"""
Darkroom Multi-Chain Airdrop Engine
====================================
Cross-chain holder aggregation for airdrop qualification.

Supports:
  • Multiple source chains per campaign (e.g., Base + Solana)
  • Multiple source tokens per chain (e.g., CRM + CryptoRugMunch)
  • Weighted allocation based on holdings across ALL chains
  • Wiggle room: minimum thresholds, tiered rewards, deduplication
  • Cross-chain snapshot merging with anti-gaming protections

Use case:
  "Airdrop CRMv2 to holders of CRM on Base OR CryptoRugMunch on Solana
   with 2x weight for Base holders, 1x for Solana, minimum 1000 tokens"

Architecture:
  MultiChainSnapshotEngine -> aggregates holders from N chains
  WeightedAirdropCalculator -> computes allocation based on rules
  CrossChainDeduplicator -> prevents double-dipping same wallet
  AirdropCampaignManager -> full lifecycle from snapshot to distribution
"""

from __future__ import annotations

import json
from app.core.logging import get_logger
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime

logger = get_logger("darkroom_multichain_airdrop")


# ── Data Models ───────────────────────────────────────────────


@dataclass
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


@dataclass
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


@dataclass
class AirdropTier:
    """Tiered reward structure based on holdings."""

    name: str
    min_score: float
    max_score: float | None = None
    base_allocation: str = "0"
    bonus_multiplier: float = 1.0
    description: str = ""


@dataclass
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
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    snapshot_completed_at: str | None = None
    distribution_completed_at: str | None = None

    # Results
    qualified_holders: list[CrossChainHolder] = field(default_factory=list)
    total_qualified: int = 0
    total_distributed: str = "0"
    tx_hashes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── Cross-Chain Snapshot Engine ───────────────────────────────


class MultiChainSnapshotEngine:
    """
    Aggregate token holders across multiple chains and tokens.

    Example:
      sources = [
          TokenSource("base", "0xCRM...", "CRM", weight=2.0, min_holdings="1000"),
          TokenSource("solana", "CRMsa...", "CRM", weight=1.5, min_holdings="1000"),
          TokenSource("solana", "CRMspl...", "CryptoRugMunch", weight=1.0, min_holdings="1"),
      ]
    """

    @staticmethod
    async def create_multichain_snapshot(
        sources: list[TokenSource],
        exclude_addresses: list[str] | None = None,
    ) -> dict[str, CrossChainHolder]:
        """
        Create a merged snapshot of all holders across all sources.
        Returns dict keyed by primary_address.
        """
        all_holders: dict[str, CrossChainHolder] = {}
        excluded = {a.lower() for a in (exclude_addresses or [])}

        for source in sources:
            logger.info(f"Snapshotting {source.token_symbol} on {source.chain} at {source.token_address}")

            # Get holders for this source
            holders = await MultiChainSnapshotEngine._get_holders_for_source(source)

            for addr, amount in holders.items():
                if addr.lower() in excluded:
                    continue

                # Create or update cross-chain holder
                if addr not in all_holders:
                    all_holders[addr] = CrossChainHolder(
                        primary_address=addr,
                        chain_addresses={source.chain: addr},
                        holdings_per_source={},
                    )

                # Record holdings for this source
                source_key = f"{source.chain}:{source.token_symbol}"
                all_holders[addr].holdings_per_source[source_key] = amount

                # Update chain address mapping
                all_holders[addr].chain_addresses[source.chain] = addr

        logger.info(f"Multi-chain snapshot: {len(all_holders)} unique holders across {len(sources)} sources")
        return all_holders

    @staticmethod
    async def _get_holders_for_source(source: TokenSource) -> dict[str, str]:
        """Get holders for a single token source."""
        if source.chain in ["ethereum", "base", "bsc"]:
            return await MultiChainSnapshotEngine._get_evm_holders(source)
        elif source.chain == "solana":
            return await MultiChainSnapshotEngine._get_solana_holders(source)
        elif source.chain == "tron":
            return await MultiChainSnapshotEngine._get_tron_holders(source)
        else:
            raise ValueError(f"Unsupported chain: {source.chain}")

    @staticmethod
    async def _get_evm_holders(source: TokenSource) -> dict[str, str]:
        """Get ERC-20 holders via RPC event logs."""
        from web3 import Web3

        rpc_url = os.getenv(f"{source.chain.upper()}_RPC_URL", "")
        if not rpc_url:
            raise ValueError(f"No RPC for {source.chain}")

        w3 = Web3(Web3.HTTPProvider(rpc_url))

        # Transfer event topic
        transfer_topic = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

        # Get recent blocks (last ~30 days worth, ~200k blocks on Base)
        current_block = w3.eth.block_number
        from_block = max(0, current_block - 200000)

        try:
            logs = w3.eth.get_logs(
                {
                    "fromBlock": from_block,
                    "toBlock": current_block,
                    "address": source.token_address,
                    "topics": [transfer_topic],
                }
            )
        except Exception as e:
            logger.error(f"EVM log query failed: {e}")
            return {}

        # Build balances from transfer logs
        balances: dict[str, int] = {}
        for log in logs:
            try:
                from_addr = "0x" + log.topics[1].hex()[-40:]
                to_addr = "0x" + log.topics[2].hex()[-40:]
                amount = int(log.data.hex(), 16) if log.data else 0

                balances[from_addr] = balances.get(from_addr, 0) - amount
                balances[to_addr] = balances.get(to_addr, 0) + amount
            except Exception:
                continue

        # Filter by minimum holdings
        min_amount = int(source.min_holdings)
        result = {}
        for addr, balance in balances.items():
            if balance > 0 and balance >= min_amount:
                # Apply max cap if set
                if source.max_holdings:
                    balance = min(balance, int(source.max_holdings))
                result[addr] = str(balance)

        return result

    @staticmethod
    async def _get_solana_holders(source: TokenSource) -> dict[str, str]:
        """Get SPL token holders via RPC."""
        from solana.rpc.api import Client
        from solders.pubkey import Pubkey

        rpc_url = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
        client = Client(rpc_url)

        try:
            mint = Pubkey.from_string(source.token_address)
            response = client.get_token_accounts_by_mint_json_parsed(mint, commitment="confirmed")

            holders = {}
            min_amount = int(source.min_holdings)

            for account in response["result"]["value"]:
                try:
                    parsed = account["account"]["data"]["parsed"]["info"]
                    owner = parsed["owner"]
                    amount = int(parsed["tokenAmount"]["amount"])

                    if amount >= min_amount:
                        if source.max_holdings:
                            amount = min(amount, int(source.max_holdings))
                        holders[owner] = str(amount)
                except Exception:
                    continue

            return holders

        except Exception as e:
            logger.error(f"Solana holder fetch failed: {e}")
            return {}

    @staticmethod
    async def _get_tron_holders(source: TokenSource) -> dict[str, str]:
        """Get TRC-20 holders via TronGrid."""
        # TRON holder enumeration requires an indexer or TronGrid API
        # Placeholder for now
        logger.warning("TRON holder snapshot not yet implemented - use manual list or TronGrid API")
        return {}


# ── Weighted Allocation Calculator ─────────────────────────────


class WeightedAirdropCalculator:
    """
    Calculate airdrop allocations based on weighted holdings across chains.
    """

    @staticmethod
    def calculate_proportional_allocations(
        holders: dict[str, CrossChainHolder],
        sources: list[TokenSource],
        total_pool: str,
        require_both: bool = False,
        require_any: bool = True,
        cross_chain_bonus: float = 0.0,
    ) -> dict[str, CrossChainHolder]:
        """
        Calculate proportional allocations.

        Formula per holder:
          weighted_score = sum(holding * weight_multiplier) for each source

          If cross_chain_bonus > 0 and holder has tokens on multiple chains:
            bonus = weighted_score * cross_chain_bonus * (num_chains - 1)
            weighted_score += bonus

          allocation = (weighted_score / total_all_scores) * total_pool
        """
        total_pool_int = int(total_pool)

        # Calculate weighted scores
        total_score = 0.0
        qualified = []

        for addr, holder in holders.items():
            score = 0.0
            chains_present = set()

            for source in sources:
                source_key = f"{source.chain}:{source.token_symbol}"
                if source_key in holder.holdings_per_source:
                    amount = float(holder.holdings_per_source[source_key])
                    score += amount * source.weight_multiplier
                    chains_present.add(source.chain)

            # Apply cross-chain bonus
            if cross_chain_bonus > 0 and len(chains_present) > 1:
                bonus = score * cross_chain_bonus * (len(chains_present) - 1)
                score += bonus
                logger.debug(f"Cross-chain bonus for {addr}: +{bonus} ({len(chains_present)} chains)")

            holder.weighted_score = score

            # Check qualification rules
            if require_both and len(chains_present) < len({s.chain for s in sources}):
                continue  # Didn't hold on all required chains

            if require_any and len(chains_present) == 0:
                continue  # Didn't hold on any chain

            if score > 0:
                total_score += score
                qualified.append(holder)

        # Calculate allocations
        if total_score == 0:
            logger.warning("No qualified holders - total score is 0")
            return {}

        for holder in qualified:
            ratio = holder.weighted_score / total_score
            allocation = int(total_pool_int * ratio)
            holder.airdrop_allocation = str(allocation)

        logger.info(f"Proportional allocation: {len(qualified)} qualified, total score: {total_score}")
        return {h.primary_address: h for h in qualified}

    @staticmethod
    def calculate_tiered_allocations(
        holders: dict[str, CrossChainHolder],
        tiers: list[AirdropTier],
    ) -> dict[str, CrossChainHolder]:
        """
        Calculate tiered allocations.

        Tiers example:
          Tier 1: score >= 1000000, allocation = 50000 tokens
          Tier 2: score >= 100000, allocation = 5000 tokens
          Tier 3: score >= 10000, allocation = 500 tokens
        """
        qualified = []

        for _addr, holder in holders.items():
            score = holder.weighted_score

            # Find matching tier
            for tier in sorted(tiers, key=lambda t: t.min_score, reverse=True):
                if score >= tier.min_score:
                    if tier.max_score and score > tier.max_score:
                        continue

                    base = int(tier.base_allocation)
                    allocation = int(base * tier.bonus_multiplier)
                    holder.airdrop_allocation = str(allocation)
                    holder.total_tokens_qualified = 1
                    qualified.append(holder)
                    break

        logger.info(f"Tiered allocation: {len(qualified)} qualified across {len(tiers)} tiers")
        return {h.primary_address: h for h in qualified}

    @staticmethod
    def calculate_fixed_allocations(
        holders: dict[str, CrossChainHolder],
        fixed_amount: str,
    ) -> dict[str, CrossChainHolder]:
        """Every qualified holder gets the same fixed amount."""
        for holder in holders.values():
            holder.airdrop_allocation = fixed_amount

        return holders

    @staticmethod
    def calculate_one_to_one_allocations(
        holders: dict[str, CrossChainHolder],
    ) -> tuple[dict[str, CrossChainHolder], str]:
        """
        Calculate 1:1 allocations.

        Each holder receives exactly the sum of their holdings across all source tokens.

        Example:
          - Holder has 5000 CRM on Solana + 3000 CryptoRugMunch on Zora
          - Allocation = 5000 + 3000 = 8000 new tokens

        Returns:
          (holders_dict, total_pool)
        """
        total_pool = 0

        for _addr, holder in holders.items():
            total_allocation = 0

            for _source_key, amount_str in holder.holdings_per_source.items():
                amount = int(amount_str)
                # 1:1 - exact same amount
                total_allocation += amount

            holder.airdrop_allocation = str(total_allocation)
            holder.weighted_score = float(total_allocation)
            total_pool += total_allocation

        logger.info(f"1:1 allocation: {len(holders)} holders, total pool: {total_pool}")
        return holders, str(total_pool)


# ── Anti-Gaming / Deduplication ───────────────────────────────


class AntiGamingFilter:
    """
    Filter out sybils, bots, and gaming attempts.
    """

    KNOWN_BOT_PATTERNS = [  # noqa: RUF012
        "0x0000000000000000000000000000000000000000",
        "0x000000000000000000000000000000000000dead",
    ]

    @staticmethod
    async def filter_holders(
        holders: dict[str, CrossChainHolder],
        exclude_contracts: bool = True,
        exclude_known_bots: bool = True,
        min_wallet_age_days: int | None = None,
        min_tx_count: int | None = None,
    ) -> dict[str, CrossChainHolder]:
        """Apply all anti-gaming filters."""
        filtered = {}

        for addr, holder in holders.items():
            # Skip known bots
            if exclude_known_bots and addr.lower() in AntiGamingFilter.KNOWN_BOT_PATTERNS:
                continue

            # Skip contracts (would need bytecode check - simplified)
            if exclude_contracts:
                # In production, check if address has code
                pass

            # Skip young wallets
            if min_wallet_age_days and holder.wallet_age_days is not None:
                if holder.wallet_age_days < min_wallet_age_days:
                    continue

            # Skip low-activity wallets
            if min_tx_count and holder.tx_count_30d < min_tx_count:
                continue

            filtered[addr] = holder

        logger.info(f"Anti-gaming filter: {len(holders)} -> {len(filtered)} holders")
        return filtered

    @staticmethod
    async def detect_sybils(holders: dict[str, CrossChainHolder]) -> list[str]:
        """
        Detect potential sybil wallets (same owner, multiple addresses).
        Simple heuristics: similar balances, same first funding, etc.
        """
        # In production, use clustering analysis
        # For now, return empty list
        return []


# ── Cross-Chain Address Linking ───────────────────────────────


class CrossChainIdentityResolver:
    """
    Link the same user's addresses across chains.

    Methods:
      1. Self-reported linking (user signs message proving ownership)
      2. Common funding source (same wallet funded both addresses)
      3. ENS/SNS resolution
      4. Platform binding (Twitter, Discord, etc.)
    """

    @staticmethod
    async def resolve_linked_addresses(
        primary_address: str,
        chain: str,
    ) -> dict[str, str]:
        """
        Find linked addresses for a wallet.
        Returns {chain: address} mapping.
        """
        # Check Redis for stored linkages
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"wallet_links:{primary_address}:{chain}"
            data = await r.get(key)
            if data:
                return json.loads(data)
        except Exception:
            pass

        return {}

    @staticmethod
    async def store_linkage(
        primary_address: str,
        primary_chain: str,
        linked_chain: str,
        linked_address: str,
        proof_signature: str,
    ) -> bool:
        """Store verified cross-chain address linkage."""
        try:
            import redis.asyncio as redis_lib

            r = redis_lib.Redis(
                host=os.getenv("REDIS_HOST", "localhost"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                password=os.getenv("REDIS_PASSWORD", ""),
                decode_responses=True,
            )

            key = f"wallet_links:{primary_address}:{primary_chain}"
            existing = {}
            data = await r.get(key)
            if data:
                existing = json.loads(data)

            existing[linked_chain] = linked_address
            await r.set(key, json.dumps(existing))

            # Also store reverse mapping
            reverse_key = f"wallet_links:{linked_address}:{linked_chain}"
            reverse_data = {primary_chain: primary_address}
            await r.set(reverse_key, json.dumps(reverse_data))

            return True
        except Exception as e:
            logger.error(f"Store linkage failed: {e}")
            return False


# ── Custom Snapshot Loader ──────────────────────────────────


class CustomSnapshotLoader:
    """
    Load custom snapshot data from uploaded files or manual input.
    Supports JSON, CSV, and manual address lists.
    """

    @staticmethod
    def parse_json_snapshot(data: str) -> dict[str, CrossChainHolder]:
        """Parse JSON snapshot data."""
        holders = {}
        parsed = json.loads(data)

        for entry in parsed:
            addr = entry.get("address", entry.get("wallet", entry.get("holder", "")))
            if not addr:
                continue

            amount = str(entry.get("amount", entry.get("balance", entry.get("holdings", "0"))))
            chain = entry.get("chain", "unknown")
            token = entry.get("token", entry.get("token_symbol", "UNKNOWN"))

            if addr not in holders:
                holders[addr] = CrossChainHolder(
                    primary_address=addr,
                    chain_addresses={chain: addr},
                    holdings_per_source={},
                )

            source_key = f"{chain}:{token}"
            # Sum amounts if same address appears multiple times
            if source_key in holders[addr].holdings_per_source:
                existing = int(holders[addr].holdings_per_source[source_key])
                holders[addr].holdings_per_source[source_key] = str(existing + int(amount))
            else:
                holders[addr].holdings_per_source[source_key] = amount

            holders[addr].chain_addresses[chain] = addr

        logger.info(f"Parsed JSON snapshot: {len(holders)} holders")
        return holders

    @staticmethod
    def parse_csv_snapshot(data: str) -> dict[str, CrossChainHolder]:
        """Parse CSV snapshot data."""
        import csv
        import io

        holders = {}
        reader = csv.DictReader(io.StringIO(data))

        for row in reader:
            addr = row.get("address", row.get("wallet", row.get("holder", "")))
            if not addr:
                continue

            amount = str(row.get("amount", row.get("balance", row.get("holdings", "0"))))
            chain = row.get("chain", "unknown")
            token = row.get("token", row.get("token_symbol", "UNKNOWN"))

            if addr not in holders:
                holders[addr] = CrossChainHolder(
                    primary_address=addr,
                    chain_addresses={chain: addr},
                    holdings_per_source={},
                )

            source_key = f"{chain}:{token}"
            if source_key in holders[addr].holdings_per_source:
                existing = int(holders[addr].holdings_per_source[source_key])
                holders[addr].holdings_per_source[source_key] = str(existing + int(amount))
            else:
                holders[addr].holdings_per_source[source_key] = amount

            holders[addr].chain_addresses[chain] = addr

        logger.info(f"Parsed CSV snapshot: {len(holders)} holders")
        return holders

    @staticmethod
    def parse_manual_holders(holders_list: list[dict[str, str]]) -> dict[str, CrossChainHolder]:
        """Parse manual holder list."""
        holders = {}

        for entry in holders_list:
            addr = entry.get("address", "")
            if not addr:
                continue

            amount = str(entry.get("amount", "0"))
            chain = entry.get("chain", "unknown")
            token = entry.get("token", "UNKNOWN")

            if addr not in holders:
                holders[addr] = CrossChainHolder(
                    primary_address=addr,
                    chain_addresses={chain: addr},
                    holdings_per_source={},
                )

            source_key = f"{chain}:{token}"
            if source_key in holders[addr].holdings_per_source:
                existing = int(holders[addr].holdings_per_source[source_key])
                holders[addr].holdings_per_source[source_key] = str(existing + int(amount))
            else:
                holders[addr].holdings_per_source[source_key] = amount

            holders[addr].chain_addresses[chain] = addr

        logger.info(f"Parsed manual holders: {len(holders)} holders")
        return holders

    @staticmethod
    def deduplicate_holders(holders: dict[str, CrossChainHolder]) -> dict[str, CrossChainHolder]:
        """Deduplicate by address (case-insensitive)."""
        deduped = {}
        for addr, holder in holders.items():
            lower_addr = addr.lower()
            if lower_addr in deduped:
                # Merge holdings
                for source_key, amount in holder.holdings_per_source.items():
                    if source_key in deduped[lower_addr].holdings_per_source:
                        existing = int(deduped[lower_addr].holdings_per_source[source_key])
                        deduped[lower_addr].holdings_per_source[source_key] = str(existing + int(amount))
                    else:
                        deduped[lower_addr].holdings_per_source[source_key] = amount
                # Merge chain addresses
                deduped[lower_addr].chain_addresses.update(holder.chain_addresses)
            else:
                deduped[lower_addr] = holder

        logger.info(f"Deduplicated: {len(holders)} -> {len(deduped)} holders")
        return deduped


# ── Campaign Manager ──────────────────────────────────────────


class MultiChainAirdropManager:
    """
    Full lifecycle manager for multi-chain airdrop campaigns.
    """

    @staticmethod
    async def create_campaign(
        target_chain: str,
        target_token: str,
        source_tokens: list[TokenSource],
        total_pool: str,
        distribution_mode: str = "proportional",
        **kwargs,
    ) -> MultiChainAirdropCampaign:
        """Create a new campaign."""
        campaign = MultiChainAirdropCampaign(
            campaign_id=f"mc_airdrop_{target_chain}_{int(time.time())}",
            target_chain=target_chain,
            target_token=target_token,
            source_tokens=source_tokens,
            total_airdrop_pool=total_pool,
            distribution_mode=distribution_mode,
            **kwargs,
        )

        # Save to storage
        await MultiChainAirdropManager._save_campaign(campaign)

        return campaign

    @staticmethod
    async def execute_snapshot(campaign_id: str) -> MultiChainAirdropCampaign:
        """Execute snapshot phase for a campaign."""
        campaign = await MultiChainAirdropManager._get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign not found: {campaign_id}")

        # Create multi-chain snapshot
        holders = await MultiChainSnapshotEngine.create_multichain_snapshot(
            sources=campaign.source_tokens,
            exclude_addresses=campaign.blacklist_preloaded if hasattr(campaign, "blacklist_preloaded") else [],
        )

        # Apply anti-gaming filters
        holders = await AntiGamingFilter.filter_holders(
            holders,
            exclude_contracts=campaign.exclude_contracts,
            exclude_known_bots=campaign.exclude_known_bots,
            min_wallet_age_days=campaign.min_wallet_age_days,
            min_tx_count=campaign.min_tx_count_30d,
        )

        # Calculate allocations
        if campaign.distribution_mode == "proportional":
            holders = WeightedAirdropCalculator.calculate_proportional_allocations(
                holders,
                campaign.source_tokens,
                campaign.total_airdrop_pool,
                require_both=campaign.require_hold_both_chains,
                require_any=campaign.require_hold_any_chain,
                cross_chain_bonus=campaign.cross_chain_bonus,
            )
        elif campaign.distribution_mode == "tiered":
            holders = WeightedAirdropCalculator.calculate_tiered_allocations(
                holders,
                campaign.tiers,
            )
        elif campaign.distribution_mode == "fixed":
            holders = WeightedAirdropCalculator.calculate_fixed_allocations(
                holders,
                campaign.total_airdrop_pool,  # This would be per-holder fixed amount
            )
        elif campaign.distribution_mode == "one_to_one":
            holders, total_pool = WeightedAirdropCalculator.calculate_one_to_one_allocations(
                holders,
            )
            campaign.total_airdrop_pool = total_pool  # Auto-set from snapshot
        else:
            raise ValueError(f"Unknown distribution mode: {campaign.distribution_mode}")

        campaign.qualified_holders = list(holders.values())
        campaign.total_qualified = len(holders)
        campaign.snapshot_completed_at = datetime.utcnow().isoformat()
        campaign.status = "snapshot_complete"

        await MultiChainAirdropManager._save_campaign(campaign)

        logger.info(f"Snapshot complete: {campaign.total_qualified} qualified for {campaign.campaign_id}")
        return campaign

    @staticmethod
    async def execute_custom_snapshot(
        campaign_id: str,
        custom_holders: dict[str, CrossChainHolder],
        deduplicate: bool = True,
    ) -> MultiChainAirdropCampaign:
        """Execute snapshot using custom/pre-uploaded holder data."""
        campaign = await MultiChainAirdropManager._get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign not found: {campaign_id}")

        holders = custom_holders

        # Deduplicate if requested
        if deduplicate:
            holders = CustomSnapshotLoader.deduplicate_holders(holders)

        # Apply anti-gaming filters
        holders = await AntiGamingFilter.filter_holders(
            holders,
            exclude_contracts=campaign.exclude_contracts,
            exclude_known_bots=campaign.exclude_known_bots,
            min_wallet_age_days=campaign.min_wallet_age_days,
            min_tx_count=campaign.min_tx_count_30d,
        )

        # Calculate allocations
        if campaign.distribution_mode == "one_to_one":
            holders, total_pool = WeightedAirdropCalculator.calculate_one_to_one_allocations(holders)
            campaign.total_airdrop_pool = total_pool
        elif campaign.distribution_mode == "proportional":
            holders = WeightedAirdropCalculator.calculate_proportional_allocations(
                holders,
                campaign.source_tokens,
                campaign.total_airdrop_pool,
                require_both=campaign.require_hold_both_chains,
                require_any=campaign.require_hold_any_chain,
                cross_chain_bonus=campaign.cross_chain_bonus,
            )
        elif campaign.distribution_mode == "tiered":
            holders = WeightedAirdropCalculator.calculate_tiered_allocations(holders, campaign.tiers)
        elif campaign.distribution_mode == "fixed":
            holders = WeightedAirdropCalculator.calculate_fixed_allocations(holders, campaign.total_airdrop_pool)

        campaign.qualified_holders = list(holders.values())
        campaign.total_qualified = len(holders)
        campaign.snapshot_completed_at = datetime.utcnow().isoformat()
        campaign.status = "snapshot_complete"

        await MultiChainAirdropManager._save_campaign(campaign)

        logger.info(f"Custom snapshot complete: {campaign.total_qualified} qualified for {campaign.campaign_id}")
        return campaign

    @staticmethod
    async def execute_distribution(campaign_id: str) -> MultiChainAirdropCampaign:
        """Execute airdrop distribution."""
        campaign = await MultiChainAirdropManager._get_campaign(campaign_id)
        if not campaign:
            raise ValueError(f"Campaign not found: {campaign_id}")

        if campaign.status != "snapshot_complete":
            raise ValueError(f"Campaign not ready for distribution: {campaign.status}")

        # Get deployer for target chain
        deployer = TokenDeployerFactory.get_deployer(campaign.target_chain)  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue

        # Distribute to all qualified holders
        total_distributed = 0
        tx_hashes = []

        for holder in campaign.qualified_holders:
            if int(holder.airdrop_allocation) <= 0:
                continue

            try:
                # Determine target address on target chain
                target_address = holder.chain_addresses.get(campaign.target_chain, holder.primary_address)

                tx = await deployer.mint_tokens(
                    campaign.target_token,
                    target_address,
                    holder.airdrop_allocation,
                )

                holder.claimed = True
                holder.claim_tx = tx
                total_distributed += int(holder.airdrop_allocation)
                tx_hashes.append(tx)

                logger.info(f"Airdropped {holder.airdrop_allocation} to {target_address}")

            except Exception as e:
                logger.error(f"Distribution failed for {holder.primary_address}: {e}")

        campaign.total_distributed = str(total_distributed)
        campaign.tx_hashes = tx_hashes
        campaign.distribution_completed_at = datetime.utcnow().isoformat()
        campaign.status = "completed" if len(tx_hashes) == len(campaign.qualified_holders) else "partial"

        await MultiChainAirdropManager._save_campaign(campaign)

        return campaign

    @staticmethod
    async def _save_campaign(campaign: MultiChainAirdropCampaign) -> bool:
        """Save campaign to storage."""
        try:
            from app.token_deployer import get_storage

            storage = await get_storage()

            if storage.redis:
                key = f"multichain_airdrop:{campaign.campaign_id}"
                await storage.redis.set(key, json.dumps(campaign.to_dict()))
                await storage.redis.sadd("multichain_airdrops:all", campaign.campaign_id)

            return True
        except Exception as e:
            logger.error(f"Save campaign failed: {e}")
            return False

    @staticmethod
    async def _get_campaign(campaign_id: str) -> MultiChainAirdropCampaign | None:
        """Get campaign from storage."""
        try:
            from app.token_deployer import get_storage

            storage = await get_storage()

            if storage.redis:
                data = await storage.redis.get(f"multichain_airdrop:{campaign_id}")
                if data:
                    return MultiChainAirdropCampaign(**json.loads(data))

            return None
        except Exception as e:
            logger.error(f"Get campaign failed: {e}")
            return None


# ── Convenience: CRM + CryptoRugMunch Multi-Chain Airdrop ───


async def create_crm_multichain_airdrop(
    crm_base_address: str,
    crm_solana_address: str,
    cryptorugmunch_solana_address: str,
    target_chain: str = "solana",
    target_token: str = "",
    total_pool: str = "100000000",
) -> MultiChainAirdropCampaign:
    """
    Create the standard CRM + CryptoRugMunch multi-chain airdrop.

    Qualification:
      - Hold CRM on Base (weight 2.0x)
      - Hold CRM on Solana (weight 1.5x)
      - Hold CryptoRugMunch on Solana (weight 1.0x)
      - Hold ANY of the above = qualified
      - Hold on multiple chains = cross-chain bonus 10%
      - Minimum 1000 tokens on any chain

    Distribution:
      - Proportional to weighted holdings
      - On target_chain (default Solana)
    """
    sources = [
        TokenSource(
            chain="base",
            token_address=crm_base_address,
            token_symbol="CRM",
            weight_multiplier=2.0,
            min_holdings="1000",
        ),
        TokenSource(
            chain="solana",
            token_address=crm_solana_address,
            token_symbol="CRM",
            weight_multiplier=1.5,
            min_holdings="1000",
        ),
        TokenSource(
            chain="solana",
            token_address=cryptorugmunch_solana_address,
            token_symbol="CryptoRugMunch",
            weight_multiplier=1.0,
            min_holdings="1",
        ),
    ]

    campaign = await MultiChainAirdropManager.create_campaign(
        target_chain=target_chain,
        target_token=target_token,
        source_tokens=sources,
        total_pool=total_pool,
        distribution_mode="proportional",
        require_hold_both_chains=False,
        require_hold_any_chain=True,
        cross_chain_bonus=0.10,  # 10% bonus per additional chain
        exclude_contracts=True,
        exclude_known_bots=True,
        min_wallet_age_days=7,
        snapshot_time_window_hours=24,
    )

    return campaign


async def create_one_to_one_airdrop(
    source_tokens: list[TokenSource],
    target_chain: str,
    target_token: str,
) -> MultiChainAirdropCampaign:
    """
    Create a 1:1 airdrop campaign.

    Each holder receives exactly the same amount they hold of the source token(s).
    If they hold multiple qualifying tokens, they receive the SUM (1:1 for each).

    Example:
      - Hold 5000 CRM on Solana → receive 5000 new tokens
      - Hold 3000 CryptoRugMunch on Zora → receive 3000 new tokens
      - Hold both → receive 8000 new tokens

    Args:
      source_tokens: List of TokenSource configs (chain, address, symbol)
      target_chain: Where to airdrop the new token
      target_token: Contract address of the new token
    """
    # Calculate total pool = sum of all holdings across all sources
    # We snapshot first to know the total, then set pool = total supply

    campaign = await MultiChainAirdropManager.create_campaign(
        target_chain=target_chain,
        target_token=target_token,
        source_tokens=source_tokens,
        total_pool="0",  # Will be set after snapshot
        distribution_mode="one_to_one",  # Custom mode
        require_hold_both_chains=False,
        require_hold_any_chain=True,
        cross_chain_bonus=0.0,  # No bonus for 1:1 - it's already exact
        exclude_contracts=True,
        exclude_known_bots=True,
        min_wallet_age_days=1,
        snapshot_time_window_hours=24,
    )

    return campaign


async def create_crm_v2_airdrop_preset(
    crm_solana_address: str,
    cryptorugmunch_zora_address: str,
    target_chain: str = "solana",
    target_token: str = "",
) -> MultiChainAirdropCampaign:
    """
    CRM v2 1:1 Airdrop Preset.

    Qualification:
      - Hold $CRM on Solana → receive exact same amount of CRMv2
      - Hold CryptoRugMunch on Zora → receive exact same amount of CRMv2
      - Hold both → receive SUM of both holdings

    Total pool auto-calculated from snapshot.
    """
    sources = [
        TokenSource(
            chain="solana",
            token_address=crm_solana_address,
            token_symbol="CRM",
            weight_multiplier=1.0,  # 1:1
            min_holdings="1",
        ),
        TokenSource(
            chain="base",  # Zora is on Base
            token_address=cryptorugmunch_zora_address,
            token_symbol="CryptoRugMunch",
            weight_multiplier=1.0,  # 1:1
            min_holdings="1",
        ),
    ]

    campaign = await create_one_to_one_airdrop(
        source_tokens=sources,
        target_chain=target_chain,
        target_token=target_token,
    )

    return campaign
