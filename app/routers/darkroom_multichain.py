"""
Darkroom Multi-Chain Airdrop Admin Router
==========================================
Admin endpoints for cross-chain airdrop campaigns.

Supports:
  • Multi-source snapshots (Base + Solana + any chain)
  • Weighted allocation based on holdings
  • Cross-chain bonuses for multi-chain holders
  • CRM + CryptoRugMunch specific presets

All endpoints require X-Admin-Key header.
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.multichain_airdrop import (
    CrossChainIdentityResolver,
    MultiChainAirdropManager,
    TokenSource,
    create_crm_multichain_airdrop,
)
from app.token_deployer import get_storage

logger = logging.getLogger("darkroom_multichain_admin")

router = APIRouter(prefix="/api/v1/admin/tokens/multichain", tags=["darkroom-multichain"])


# ── Auth helper ───────────────────────────────────────────────


async def _verify_admin(request: Request):
    admin_key = os.getenv("ADMIN_API_KEY", "dev-key-change-me")
    key = request.headers.get("X-Admin-Key", "")
    if key != admin_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True


# ── Request Models ────────────────────────────────────────────


class TokenSourceConfig(BaseModel):
    chain: str
    token_address: str
    token_symbol: str
    weight_multiplier: float = 1.0
    min_holdings: str = "0"
    max_holdings: str | None = None


class CreateCampaignRequest(BaseModel):
    target_chain: str
    target_token: str
    source_tokens: list[TokenSourceConfig]
    total_pool: str
    distribution_mode: str = "proportional"
    require_hold_both_chains: bool = False
    require_hold_any_chain: bool = True
    cross_chain_bonus: float = 0.0
    exclude_contracts: bool = True
    exclude_known_bots: bool = True
    min_wallet_age_days: int | None = None
    min_tx_count_30d: int | None = None
    snapshot_time_window_hours: int = 24


class CRMPresetRequest(BaseModel):
    crm_base_address: str | None = None
    crm_solana_address: str
    cryptorugmunch_zora_address: str  # Zora tokens are on Base
    target_chain: str = "solana"
    target_token: str = ""
    total_pool: str | None = None  # Auto-calculated for 1:1
    mode: str = "one_to_one"  # one_to_one, proportional, tiered


class CRMv2LaunchRequest(BaseModel):
    crm_solana_address: str
    cryptorugmunch_zora_address: str
    target_chain: str = "solana"
    target_token: str = ""
    # Deploy params if target_token not provided
    deploy_new_token: bool = False
    token_name: str = "CRM Token v2"
    token_symbol: str = "CRMv2"
    decimals: int = 18
    # Anti-sniper
    anti_sniper: bool = True
    blacklist_addresses: list[str] = Field(default_factory=list)
    max_wallet_percent: float = 2.0
    max_tx_percent: float = 1.0
    # Team allocation
    dev_address: str | None = None
    dev_percent: float = 15.0
    marketing_address: str | None = None
    marketing_percent: float = 10.0
    treasury_address: str | None = None
    treasury_percent: float = 15.0


class ExecuteSnapshotRequest(BaseModel):
    campaign_id: str
    use_custom_snapshot: bool = False
    custom_snapshot_format: str = "json"  # json, csv, manual
    custom_snapshot_data: str | None = None  # JSON string or CSV content
    custom_snapshot_file: str | None = None  # Path to uploaded file


class UploadSnapshotRequest(BaseModel):
    campaign_id: str
    format: str = "json"  # json, csv
    data: str  # JSON array or CSV content
    deduplicate: bool = True
    validate_balances: bool = False


class ManualHoldersRequest(BaseModel):
    campaign_id: str
    holders: list[dict[str, str]]  # [{"address": "0x...", "amount": "1000", "chain": "solana", "token": "CRM"}]
    append: bool = False  # True = append to existing, False = replace


class ExecuteDistributionRequest(BaseModel):
    campaign_id: str


class LinkAddressesRequest(BaseModel):
    primary_address: str
    primary_chain: str
    linked_chain: str
    linked_address: str
    proof_signature: str = ""


class GetCampaignRequest(BaseModel):
    campaign_id: str


class TierConfig(BaseModel):
    name: str
    min_score: float
    max_score: float | None = None
    base_allocation: str
    bonus_multiplier: float = 1.0


class CreateTieredCampaignRequest(BaseModel):
    target_chain: str
    target_token: str
    source_tokens: list[TokenSourceConfig]
    tiers: list[TierConfig]
    total_pool: str
    cross_chain_bonus: float = 0.0


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/campaign/create")
async def create_campaign(request: Request, body: CreateCampaignRequest, _=Depends(_verify_admin)):
    """Create a new multi-chain airdrop campaign."""
    try:
        sources = [
            TokenSource(
                chain=s.chain,
                token_address=s.token_address,
                token_symbol=s.token_symbol,
                weight_multiplier=s.weight_multiplier,
                min_holdings=s.min_holdings,
                max_holdings=s.max_holdings,
            )
            for s in body.source_tokens
        ]

        campaign = await MultiChainAirdropManager.create_campaign(
            target_chain=body.target_chain,
            target_token=body.target_token,
            source_tokens=sources,
            total_pool=body.total_pool,
            distribution_mode=body.distribution_mode,
            require_hold_both_chains=body.require_hold_both_chains,
            require_hold_any_chain=body.require_hold_any_chain,
            cross_chain_bonus=body.cross_chain_bonus,
            exclude_contracts=body.exclude_contracts,
            exclude_known_bots=body.exclude_known_bots,
            min_wallet_age_days=body.min_wallet_age_days,
            min_tx_count_30d=body.min_tx_count_30d,
            snapshot_time_window_hours=body.snapshot_time_window_hours,
        )

        return {
            "success": True,
            "campaign": campaign.to_dict(),
        }

    except Exception as e:
        logger.error(f"Create campaign failed: {e}")
        raise HTTPException(status_code=500, detail=f"Create failed: {e!s}")


@router.post("/campaign/crm-preset")
async def create_crm_preset(request: Request, body: CRMPresetRequest, _=Depends(_verify_admin)):
    """
    Create CRM v2 airdrop campaign.

    Modes:
      - one_to_one: Holders receive exact same amount they hold (default)
      - proportional: Weighted proportional distribution

    Default (1:1):
      - Hold $CRM on Solana → receive exact same amount of CRMv2
      - Hold CryptoRugMunch on Zora → receive exact same amount of CRMv2
      - Hold both → receive SUM of both holdings
    """
    try:
        if body.mode == "one_to_one":
            from app.multichain_airdrop import create_crm_v2_airdrop_preset

            campaign = await create_crm_v2_airdrop_preset(
                crm_solana_address=body.crm_solana_address,
                cryptorugmunch_zora_address=body.cryptorugmunch_zora_address,
                target_chain=body.target_chain,
                target_token=body.target_token,
            )
            rules = {
                "mode": "1:1 exact match",
                "hold_any_of": [
                    "CRM on Solana (any amount) → receive exact same amount",
                    "CryptoRugMunch on Zora (any amount) → receive exact same amount",
                ],
                "multiple_holdings": "SUM of all holdings",
                "distribution": f"1:1 on {body.target_chain}",
            }
        else:
            # Proportional mode (legacy)
            campaign = await create_crm_multichain_airdrop(
                crm_base_address=body.crm_base_address or "",
                crm_solana_address=body.crm_solana_address,
                cryptorugmunch_solana_address=body.cryptorugmunch_zora_address,
                target_chain=body.target_chain,
                target_token=body.target_token,
                total_pool=body.total_pool or "100000000",
            )
            rules = {
                "mode": "proportional",
                "hold_any_of": [
                    "CRM on Base (>= 1000, weight 2.0x)",
                    "CRM on Solana (>= 1000, weight 1.5x)",
                    "CryptoRugMunch on Zora (>= 1, weight 1.0x)",
                ],
                "cross_chain_bonus": "10% per additional chain",
                "distribution": f"Proportional on {body.target_chain}",
            }

        return {
            "success": True,
            "campaign": campaign.to_dict(),
            "preset": "CRM v2 Airdrop",
            "qualification_rules": rules,
        }

    except Exception as e:
        logger.error(f"CRM preset failed: {e}")
        raise HTTPException(status_code=500, detail=f"Preset failed: {e!s}")


@router.post("/snapshot")
async def execute_snapshot(request: Request, body: ExecuteSnapshotRequest, _=Depends(_verify_admin)):
    """Execute snapshot phase for a campaign."""
    try:
        # Use custom snapshot if provided
        if body.use_custom_snapshot:
            from app.multichain_airdrop import CustomSnapshotLoader

            if body.custom_snapshot_format == "json" and body.custom_snapshot_data:
                holders = CustomSnapshotLoader.parse_json_snapshot(body.custom_snapshot_data)
            elif body.custom_snapshot_format == "csv" and body.custom_snapshot_data:
                holders = CustomSnapshotLoader.parse_csv_snapshot(body.custom_snapshot_data)
            else:
                raise HTTPException(status_code=400, detail="Custom snapshot data required")

            campaign = await MultiChainAirdropManager.execute_custom_snapshot(
                body.campaign_id, holders, deduplicate=True
            )
        else:
            campaign = await MultiChainAirdropManager.execute_snapshot(body.campaign_id)

        # Build summary
        holder_preview = []
        for h in campaign.qualified_holders[:10]:
            holder_preview.append(
                {
                    "address": h.primary_address,
                    "chains": list(h.chain_addresses.keys()),
                    "holdings": h.holdings_per_source,
                    "weighted_score": h.weighted_score,
                    "allocation": h.airdrop_allocation,
                }
            )

        return {
            "success": True,
            "campaign_id": campaign.campaign_id,
            "status": campaign.status,
            "total_qualified": campaign.total_qualified,
            "snapshot_completed_at": campaign.snapshot_completed_at,
            "holders_preview": holder_preview,
            "total_pool": campaign.total_airdrop_pool,
            "snapshot_source": "custom" if body.use_custom_snapshot else "on-chain",
        }

    except Exception as e:
        logger.error(f"Snapshot failed: {e}")
        raise HTTPException(status_code=500, detail=f"Snapshot failed: {e!s}")


@router.post("/snapshot/upload")
async def upload_snapshot(request: Request, body: UploadSnapshotRequest, _=Depends(_verify_admin)):
    """Upload a custom snapshot file (JSON or CSV) for a campaign."""
    try:
        from app.multichain_airdrop import CustomSnapshotLoader

        if body.format == "json":
            holders = CustomSnapshotLoader.parse_json_snapshot(body.data)
        elif body.format == "csv":
            holders = CustomSnapshotLoader.parse_csv_snapshot(body.data)
        else:
            raise HTTPException(status_code=400, detail="Format must be 'json' or 'csv'")

        if body.deduplicate:
            holders = CustomSnapshotLoader.deduplicate_holders(holders)

        # Execute custom snapshot
        campaign = await MultiChainAirdropManager.execute_custom_snapshot(
            body.campaign_id,
            holders,
            deduplicate=False,  # Already deduped
        )

        return {
            "success": True,
            "campaign_id": body.campaign_id,
            "format": body.format,
            "holders_parsed": len(holders),
            "total_qualified": campaign.total_qualified,
            "total_pool": campaign.total_airdrop_pool,
            "status": campaign.status,
        }

    except Exception as e:
        logger.error(f"Upload snapshot failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {e!s}")


@router.post("/snapshot/manual")
async def add_manual_holders(request: Request, body: ManualHoldersRequest, _=Depends(_verify_admin)):
    """Add manual holders to a campaign (or replace existing)."""
    try:
        from app.multichain_airdrop import CustomSnapshotLoader

        # Parse manual holders
        new_holders = CustomSnapshotLoader.parse_manual_holders(body.holders)

        campaign = await MultiChainAirdropManager._get_campaign(body.campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        if body.append and campaign.qualified_holders:
            # Merge with existing
            existing = {h.primary_address: h for h in campaign.qualified_holders}
            existing.update(new_holders)
            all_holders = existing
        else:
            all_holders = new_holders

        # Re-run snapshot with combined data
        campaign = await MultiChainAirdropManager.execute_custom_snapshot(
            body.campaign_id, all_holders, deduplicate=True
        )

        return {
            "success": True,
            "campaign_id": body.campaign_id,
            "holders_added": len(body.holders),
            "total_qualified": campaign.total_qualified,
            "total_pool": campaign.total_airdrop_pool,
            "status": campaign.status,
        }

    except Exception as e:
        logger.error(f"Manual holders failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed: {e!s}")


@router.post("/distribute")
async def execute_distribution(request: Request, body: ExecuteDistributionRequest, _=Depends(_verify_admin)):
    """Execute airdrop distribution to all qualified holders."""
    try:
        campaign = await MultiChainAirdropManager.execute_distribution(body.campaign_id)

        return {
            "success": True,
            "campaign_id": campaign.campaign_id,
            "status": campaign.status,
            "total_qualified": campaign.total_qualified,
            "total_distributed": campaign.total_distributed,
            "distribution_completed_at": campaign.distribution_completed_at,
            "tx_count": len(campaign.tx_hashes),
            "tx_hashes": campaign.tx_hashes[:10],  # Preview first 10
        }

    except Exception as e:
        logger.error(f"Distribution failed: {e}")
        raise HTTPException(status_code=500, detail=f"Distribution failed: {e!s}")


@router.post("/launch")
async def full_multichain_launch(request: Request, body: CRMv2LaunchRequest, _=Depends(_verify_admin)):
    """
    FULL CRM v2 LAUNCH: Deploy token + airdrop in one shot.

    If target_token is empty, deploys a new CRMv2 token first.
    Then snapshots CRM (Solana) + CryptoRugMunch (Zora) holders.
    Finally distributes 1:1 airdrop to all qualified holders.
    """
    try:
        target_token = body.target_token

        # 1. Deploy new token if needed
        if not target_token and body.deploy_new_token:
            from app.token_deployer import DeployParams, TokenDeployerFactory

            deployer = TokenDeployerFactory.get_deployer(body.target_chain)

            deploy_params = DeployParams(
                chain=body.target_chain,
                name=body.token_name,
                symbol=body.token_symbol,
                decimals=body.decimals,
                initial_supply="1000000000",  # 1B total
                mintable=True,
                burnable=True,
                blacklist_enabled=body.anti_sniper,
            )

            deployment = await deployer.deploy_token(deploy_params)
            target_token = deployment.contract_address

            # Apply anti-sniper
            if body.anti_sniper:
                from app.airdrop_engine import AntiSniperProtection

                await AntiSniperProtection.apply_protection(
                    deployer,
                    target_token,
                    deployment,
                    blacklist_addresses=body.blacklist_addresses,
                    max_wallet_percent=body.max_wallet_percent,
                    max_tx_percent=body.max_tx_percent,
                )

            # Save deployment
            storage = await get_storage()
            await storage.save(deployment)

        # 2. Create 1:1 airdrop campaign
        from app.multichain_airdrop import create_crm_v2_airdrop_preset

        campaign = await create_crm_v2_airdrop_preset(
            crm_solana_address=body.crm_solana_address,
            cryptorugmunch_zora_address=body.cryptorugmunch_zora_address,
            target_chain=body.target_chain,
            target_token=target_token,
        )

        # 3. Snapshot
        campaign = await MultiChainAirdropManager.execute_snapshot(campaign.campaign_id)

        # 4. Distribute
        if campaign.total_qualified > 0:
            campaign = await MultiChainAirdropManager.execute_distribution(campaign.campaign_id)

        return {
            "success": True,
            "campaign_id": campaign.campaign_id,
            "target_token": target_token,
            "status": campaign.status,
            "total_qualified": campaign.total_qualified,
            "total_distributed": campaign.total_distributed,
            "total_pool": campaign.total_airdrop_pool,
            "tx_count": len(campaign.tx_hashes),
            "phases": {
                "token_deployed": body.deploy_new_token and not body.target_token,
                "snapshot_complete": True,
                "distribution_complete": campaign.status in ["completed", "partial"],
            },
        }

    except Exception as e:
        logger.error(f"Full launch failed: {e}")
        raise HTTPException(status_code=500, detail=f"Launch failed: {e!s}")


@router.get("/campaign/{campaign_id}")
async def get_campaign(request: Request, campaign_id: str, _=Depends(_verify_admin)):
    """Get campaign details and status."""
    try:
        campaign = await MultiChainAirdropManager._get_campaign(campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        return {
            "campaign": campaign.to_dict(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get campaign failed: {e}")
        raise HTTPException(status_code=500, detail=f"Get failed: {e!s}")


@router.get("/campaign/{campaign_id}/holders")
async def get_campaign_holders(
    request: Request, campaign_id: str, limit: int = 100, offset: int = 0, _=Depends(_verify_admin)
):
    """Get qualified holders for a campaign."""
    try:
        campaign = await MultiChainAirdropManager._get_campaign(campaign_id)
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        holders = campaign.qualified_holders[offset : offset + limit]

        return {
            "campaign_id": campaign_id,
            "total": campaign.total_qualified,
            "offset": offset,
            "limit": limit,
            "holders": [h.to_dict() for h in holders],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get holders failed: {e}")
        raise HTTPException(status_code=500, detail=f"Get failed: {e!s}")


@router.post("/link-addresses")
async def link_addresses(request: Request, body: LinkAddressesRequest, _=Depends(_verify_admin)):
    """Manually link cross-chain addresses for a user."""
    try:
        result = await CrossChainIdentityResolver.store_linkage(
            primary_address=body.primary_address,
            primary_chain=body.primary_chain,
            linked_chain=body.linked_chain,
            linked_address=body.linked_address,
            proof_signature=body.proof_signature,
        )

        return {
            "success": result,
            "primary": f"{body.primary_chain}:{body.primary_address}",
            "linked": f"{body.linked_chain}:{body.linked_address}",
        }

    except Exception as e:
        logger.error(f"Link addresses failed: {e}")
        raise HTTPException(status_code=500, detail=f"Link failed: {e!s}")


@router.get("/link-addresses/{address}/{chain}")
async def get_linked_addresses(request: Request, address: str, chain: str, _=Depends(_verify_admin)):
    """Get linked addresses for a wallet."""
    try:
        links = await CrossChainIdentityResolver.resolve_linked_addresses(address, chain)

        return {
            "primary": f"{chain}:{address}",
            "linked_addresses": links,
        }

    except Exception as e:
        logger.error(f"Get links failed: {e}")
        raise HTTPException(status_code=500, detail=f"Get failed: {e!s}")


@router.post("/campaign/tiered")
async def create_tiered_campaign(request: Request, body: CreateTieredCampaignRequest, _=Depends(_verify_admin)):
    """Create a tiered multi-chain airdrop campaign."""
    try:
        sources = [
            TokenSource(
                chain=s.chain,
                token_address=s.token_address,
                token_symbol=s.token_symbol,
                weight_multiplier=s.weight_multiplier,
                min_holdings=s.min_holdings,
                max_holdings=s.max_holdings,
            )
            for s in body.source_tokens
        ]

        from app.multichain_airdrop import AirdropTier

        tiers = [
            AirdropTier(
                name=t.name,
                min_score=t.min_score,
                max_score=t.max_score,
                base_allocation=t.base_allocation,
                bonus_multiplier=t.bonus_multiplier,
            )
            for t in body.tiers
        ]

        campaign = await MultiChainAirdropManager.create_campaign(
            target_chain=body.target_chain,
            target_token=body.target_token,
            source_tokens=sources,
            total_pool=body.total_pool,
            distribution_mode="tiered",
            tiers=tiers,
            cross_chain_bonus=body.cross_chain_bonus,
        )

        return {
            "success": True,
            "campaign": campaign.to_dict(),
        }

    except Exception as e:
        logger.error(f"Tiered campaign failed: {e}")
        raise HTTPException(status_code=500, detail=f"Create failed: {e!s}")
