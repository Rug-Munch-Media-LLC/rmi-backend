"""Multi-chain airdrop campaign manager and convenience functions."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from app.core.logging import get_logger
from app.domains.airdrop.multichain.calculator import WeightedAirdropCalculator
from app.domains.airdrop.multichain.filters import AntiGamingFilter
from app.domains.airdrop.multichain.loader import CustomSnapshotLoader
from app.domains.airdrop.multichain.models import (
    CrossChainHolder,
    MultiChainAirdropCampaign,
    TokenSource,
)
from app.domains.airdrop.multichain.snapshot import MultiChainSnapshotEngine

logger = get_logger("darkroom_multichain_airdrop")


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
        campaign.snapshot_completed_at = datetime.now(UTC).isoformat()
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
        campaign.snapshot_completed_at = datetime.now(UTC).isoformat()
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
        deployer = TokenDeployerFactory.get_deployer(campaign.target_chain)

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

            except (ValueError, KeyError, TypeError) as e:
                logger.debug("execute_distribution_error", exc_info=True)
                logger.error(f"Distribution failed for {holder.primary_address}: {e}")

        campaign.total_distributed = str(total_distributed)
        campaign.tx_hashes = tx_hashes
        campaign.distribution_completed_at = datetime.now(UTC).isoformat()
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
        except (json.JSONDecodeError, ConnectionError, OSError, ValueError) as e:
            logger.debug("_save_campaign_error", exc_info=True)
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
        except (json.JSONDecodeError, ConnectionError, OSError, ValueError, KeyError, TypeError) as e:
            logger.debug("_get_campaign_error", exc_info=True)
            logger.error(f"Get campaign failed: {e}")
            return None


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
