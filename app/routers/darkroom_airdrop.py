"""
Darkroom Airdrop Admin Router
==============================
Admin-only endpoints for airdrop operations:
  • Snapshot creation from existing tokens
  • Team/dev token allocation with vesting
  • Anti-sniper protection configuration
  • Batch airdrop execution
  • Vesting management

All endpoints require X-Admin-Key header.
"""

import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.airdrop_engine import (
    AirdropCampaign,
    AirdropDistributor,
    AirdropStorage,
    AntiSniperProtection,
    SnapshotEngine,
    TeamAllocation,
    create_full_token_with_protection,
)
from app.token_deployer import TokenDeployerFactory, get_storage

logger = logging.getLogger("darkroom_airdrop_admin")

router = APIRouter(prefix="/api/v1/admin/tokens/airdrop", tags=["darkroom-airdrop"])


# ── Auth helper ───────────────────────────────────────────────


async def _verify_admin(request: Request):
    admin_key = os.getenv("ADMIN_API_KEY", "dev-key-change-me")
    key = request.headers.get("X-Admin-Key", "")
    if key != admin_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True


# ── Request Models ────────────────────────────────────────────


class SnapshotRequest(BaseModel):
    source_token: str
    source_chain: str = Field(..., description="ethereum, base, bsc, solana")
    min_holdings: str = "0"
    excluded_addresses: list[str] = Field(default_factory=list)
    block_number: int | None = None


class AirdropExecuteRequest(BaseModel):
    deployment_id: str
    snapshot_id: str | None = None
    distribution_type: str = "snapshot"  # snapshot, manual, team
    recipients: list[dict[str, str]] = Field(default_factory=list)
    # For manual: [{"address": "0x...", "amount": "1000", "reason": "marketing"}]


class TeamAllocationRequest(BaseModel):
    deployment_id: str
    allocations: list[dict[str, Any]] = Field(
        ...,
        description="""
        [
            {"address": "0x...", "role": "dev", "percent": 15, "immediate": 20,
             "vested": 80, "cliff_months": 6, "vesting_months": 24},
            {"address": "0x...", "role": "marketing", "percent": 10, "immediate": 50,
             "vested": 50, "cliff_months": 3, "vesting_months": 12}
        ]
    """,
    )


class AntiSniperRequest(BaseModel):
    deployment_id: str
    blacklist_addresses: list[str] = Field(default_factory=list)
    trading_delay_blocks: int = 0
    max_wallet_percent: float = 2.0
    max_tx_percent: float = 1.0
    enable_trading_at_block: int | None = None


class FullLaunchRequest(BaseModel):
    chain: str
    name: str
    symbol: str
    decimals: int = 18
    initial_supply: str = "1000000000"  # 1B default
    team_allocations: list[dict[str, Any]] = Field(default_factory=list)
    airdrop_source_token: str | None = None
    airdrop_source_chain: str | None = None
    anti_sniper: bool = True
    trading_delay_blocks: int = 0
    max_wallet_percent: float = 2.0
    max_tx_percent: float = 1.0
    blacklist_addresses: list[str] = Field(default_factory=list)
    # Quick team presets
    dev_percent: float | None = None
    marketing_percent: float | None = None
    treasury_percent: float | None = None
    airdrop_percent: float | None = None
    dev_address: str | None = None
    marketing_address: str | None = None
    treasury_address: str | None = None


class VestingClaimRequest(BaseModel):
    schedule_id: str


class EnableTradingRequest(BaseModel):
    deployment_id: str


# ── Endpoints ─────────────────────────────────────────────────


@router.post("/snapshot")
async def create_snapshot(request: Request, body: SnapshotRequest, _=Depends(_verify_admin)):
    """Create a snapshot of token holders for airdrop eligibility."""
    try:
        if body.source_chain in ["ethereum", "base", "bsc"]:
            rpc = os.getenv(f"{body.source_chain.upper()}_RPC_URL", "")
            if not rpc:
                raise HTTPException(status_code=400, detail=f"No RPC configured for {body.source_chain}")

            snapshot = await SnapshotEngine.create_evm_snapshot(
                token_address=body.source_token,
                chain=body.source_chain,
                rpc_url=rpc,
                min_holdings=body.min_holdings,
                excluded_addresses=body.excluded_addresses,
                block_number=body.block_number,
            )
        elif body.source_chain == "solana":
            rpc = os.getenv("SOLANA_RPC_URL", "")
            if not rpc:
                raise HTTPException(status_code=400, detail="No Solana RPC configured")

            snapshot = await SnapshotEngine.create_solana_snapshot(
                token_address=body.source_token,
                rpc_url=rpc,
                min_holdings=body.min_holdings,
                excluded_addresses=body.excluded_addresses,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported chain: {body.source_chain}")

        await AirdropStorage.save_snapshot(snapshot)

        return {
            "success": True,
            "snapshot": {
                "snapshot_id": snapshot.snapshot_id,
                "source_token": snapshot.source_token,
                "source_chain": snapshot.source_chain,
                "block_number": snapshot.block_number,
                "total_holders": snapshot.total_holders,
                "total_supply_snapshotted": snapshot.total_supply_snapshotted,
                "timestamp": snapshot.timestamp,
            },
            "holders_preview": [{"address": h.address, "amount": h.amount} for h in snapshot.holders[:10]],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Snapshot failed: {e}")
        raise HTTPException(status_code=500, detail=f"Snapshot failed: {e!s}")


@router.post("/execute")
async def execute_airdrop(request: Request, body: AirdropExecuteRequest, _=Depends(_verify_admin)):
    """Execute airdrop distribution."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)

        # Get recipients
        recipients = []
        if body.snapshot_id:
            # Load from snapshot
            snapshot_data = None
            if storage.redis:
                data = await storage.redis.get(f"airdrop_snapshot:{body.snapshot_id}")
                if data:
                    snapshot_data = json.loads(data)

            if not snapshot_data:
                raise HTTPException(status_code=404, detail="Snapshot not found")

            from app.airdrop_engine import AirdropRecipient

            recipients = [AirdropRecipient(**r) for r in snapshot_data.get("holders", [])]
        elif body.recipients:
            from app.airdrop_engine import AirdropRecipient

            recipients = [AirdropRecipient(**r) for r in body.recipients]
        else:
            raise HTTPException(status_code=400, detail="No recipients provided (need snapshot_id or recipients)")

        # Execute based on chain
        if deployment.chain in ["ethereum", "base", "bsc"]:
            result = await AirdropDistributor.execute_evm_airdrop(deployer, deployment.contract_address, recipients)
        elif deployment.chain == "solana":
            result = await AirdropDistributor.execute_solana_airdrop(deployer, deployment.contract_address, recipients)
        else:
            raise HTTPException(status_code=400, detail=f"Airdrop not supported for {deployment.chain}")

        # Save campaign
        campaign = AirdropCampaign(
            campaign_id=f"airdrop_{body.deployment_id}_{int(time.time())}",
            deployment_id=body.deployment_id,
            snapshot_id=body.snapshot_id or "manual",
            chain=deployment.chain,
            status="completed" if result["failed"] == 0 else "partial",
            distribution_type=body.distribution_type,
            recipients=recipients,
            total_amount=str(sum(int(r.amount) for r in recipients)),
            distributed_amount=str(sum(int(r.amount) for r in recipients if r.claimed)),
            tx_hashes=result["tx_hashes"],
        )
        await AirdropStorage.save_campaign(campaign)

        return {
            "success": True,
            "campaign_id": campaign.campaign_id,
            "result": result,
            "total_recipients": len(recipients),
            "distributed": campaign.distributed_amount,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Airdrop execution failed: {e}")
        raise HTTPException(status_code=500, detail=f"Airdrop failed: {e!s}")


@router.post("/team")
async def allocate_team(request: Request, body: TeamAllocationRequest, _=Depends(_verify_admin)):
    """Allocate team/dev tokens with optional vesting."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)

        result = await TeamAllocation.allocate_team_tokens(deployer, deployment, body.allocations)

        return {
            "success": True,
            "deployment_id": body.deployment_id,
            "allocations": result["allocations"],
            "total_allocated": str(result["total_allocated"]),
            "tx_hashes": result["tx_hashes"],
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Team allocation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Team allocation failed: {e!s}")


@router.post("/antisniper")
async def apply_antisniper(request: Request, body: AntiSniperRequest, _=Depends(_verify_admin)):
    """Apply anti-sniper protection to a deployed token."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)

        result = await AntiSniperProtection.apply_protection(
            deployer,
            deployment.contract_address,
            deployment,
            blacklist_addresses=body.blacklist_addresses,
            trading_delay_blocks=body.trading_delay_blocks,
            max_wallet_percent=body.max_wallet_percent,
            max_tx_percent=body.max_tx_percent,
        )

        # Update deployment record
        await storage.update_status(body.deployment_id, "anti_sniper_applied", {"anti_sniper": result})

        return {
            "success": True,
            "deployment_id": body.deployment_id,
            "protection_applied": result,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Anti-sniper failed: {e}")
        raise HTTPException(status_code=500, detail=f"Anti-sniper failed: {e!s}")


@router.post("/launch")
async def full_launch(request: Request, body: FullLaunchRequest, _=Depends(_verify_admin)):
    """
    Full token launch with everything: deploy, anti-sniper, team allocation, airdrop.
    This is the one-shot launch endpoint for CRM v2 or any new token.
    """
    try:
        # Build team allocations from quick presets if provided
        team_allocs = body.team_allocations or []

        if body.dev_percent and body.dev_address:
            team_allocs.append(
                {
                    "address": body.dev_address,
                    "role": "development",
                    "percent": body.dev_percent,
                    "immediate": 20,
                    "vested": 80,
                    "cliff_months": 6,
                    "vesting_months": 24,
                }
            )

        if body.marketing_percent and body.marketing_address:
            team_allocs.append(
                {
                    "address": body.marketing_address,
                    "role": "marketing",
                    "percent": body.marketing_percent,
                    "immediate": 50,
                    "vested": 50,
                    "cliff_months": 3,
                    "vesting_months": 12,
                }
            )

        if body.treasury_percent and body.treasury_address:
            team_allocs.append(
                {
                    "address": body.treasury_address,
                    "role": "treasury",
                    "percent": body.treasury_percent,
                    "immediate": 0,
                    "vested": 100,
                    "cliff_months": 12,
                    "vesting_months": 36,
                }
            )

        result = await create_full_token_with_protection(
            chain=body.chain,
            name=body.name,
            symbol=body.symbol,
            decimals=body.decimals,
            initial_supply=body.initial_supply,
            team_allocations=team_allocs,
            airdrop_source_token=body.airdrop_source_token,
            airdrop_source_chain=body.airdrop_source_chain,
            anti_sniper=body.anti_sniper,
            trading_delay_blocks=body.trading_delay_blocks,
            max_wallet_percent=body.max_wallet_percent,
            max_tx_percent=body.max_tx_percent,
            blacklist_addresses=body.blacklist_addresses,
        )

        return {
            "success": True,
            "launch_complete": True,
            "deployment": result.get("deployment"),
            "anti_sniper": result.get("anti_sniper"),
            "team_allocation": result.get("team_allocation"),
            "airdrop": result.get("airdrop"),
            "errors": result.get("errors", []),
            "explorer_url": _get_explorer_url(body.chain, result["deployment"]["contract_address"])
            if result.get("deployment")
            else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Full launch failed: {e}")
        raise HTTPException(status_code=500, detail=f"Launch failed: {e!s}")


@router.post("/trading/enable")
async def enable_trading(request: Request, body: EnableTradingRequest, _=Depends(_verify_admin)):
    """Enable trading for a token (after anti-sniper delay)."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.set_trading_enabled(deployment.contract_address, True)

        await storage.update_status(body.deployment_id, "trading_enabled", {"trading_enabled_tx": tx_hash})

        return {
            "success": True,
            "tx_hash": tx_hash,
            "trading_enabled": True,
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Enable trading failed: {e}")
        raise HTTPException(status_code=500, detail=f"Enable trading failed: {e!s}")


@router.get("/campaign/{campaign_id}")
async def get_campaign(request: Request, campaign_id: str, _=Depends(_verify_admin)):
    """Get airdrop campaign status."""
    try:
        campaign = await AirdropStorage.get_campaign(campaign_id)
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


@router.get("/vesting/{schedule_id}")
async def get_vesting(request: Request, schedule_id: str, _=Depends(_verify_admin)):
    """Get vesting schedule details."""
    try:
        from app.token_deployer import get_storage

        storage = await get_storage()

        data = None
        if storage.redis:
            data = await storage.redis.get(f"vesting:{schedule_id}")

        if not data:
            raise HTTPException(status_code=404, detail="Vesting schedule not found")

        return {
            "vesting": json.loads(data),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get vesting failed: {e}")
        raise HTTPException(status_code=500, detail=f"Get failed: {e!s}")


# ── Helpers ───────────────────────────────────────────────────


def _get_explorer_url(chain: str, address_or_tx: str, is_tx: bool = False) -> str:
    explorers = {
        "ethereum": "https://etherscan.io",
        "base": "https://basescan.org",
        "bsc": "https://bscscan.com",
        "solana": "https://solscan.io",
        "tron": "https://tronscan.org/#",
    }
    base = explorers.get(chain, "")
    if not base:
        return ""

    if chain == "tron":
        path = "transaction" if is_tx else "contract"
        return f"{base}/{path}/{address_or_tx}"

    path = "tx" if is_tx else "address"
    return f"{base}/{path}/{address_or_tx}"
