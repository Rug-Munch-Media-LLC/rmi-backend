"""
Darkroom Token Admin Router
=============================
Admin-only endpoints for deploying, minting, and managing tokens
across multiple chains. Includes blacklist/anti-bot controls.

All endpoints require X-Admin-Key header.

Routes:
  POST /api/v1/admin/tokens/deploy       - Deploy new token
  POST /api/v1/admin/tokens/mint       - Mint additional tokens
  POST /api/v1/admin/tokens/burn       - Burn tokens
  POST /api/v1/admin/tokens/transfer   - Transfer ownership
  POST /api/v1/admin/tokens/renounce   - Renounce ownership
  POST /api/v1/admin/tokens/blacklist  - Add to blacklist
  POST /api/v1/admin/tokens/unblacklist - Remove from blacklist
  GET  /api/v1/admin/tokens/list       - List all deployments
  GET  /api/v1/admin/tokens/{id}       - Get deployment details
  GET  /api/v1/admin/tokens/chains     - List supported chains
"""

import logging
import os

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.token_deployer import (
    DeployParams,
    TokenDeployerFactory,
    get_storage,
)

logger = logging.getLogger("darkroom_token_admin")

router = APIRouter(prefix="/api/v1/admin/tokens", tags=["darkroom-tokens"])


# ── Auth helper ───────────────────────────────────────────────


async def _verify_admin(request: Request):
    """Verify admin access with API key."""
    admin_key = os.getenv("ADMIN_API_KEY", "dev-key-change-me")
    key = request.headers.get("X-Admin-Key", "")
    if key != admin_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True


# ── Request Models ──────────────────────────────────────────


class DeployTokenRequest(BaseModel):
    chain: str = Field(..., description="Chain: ethereum, base, bsc, solana, tron")
    name: str = Field(..., min_length=1, max_length=50)
    symbol: str = Field(..., min_length=1, max_length=10)
    decimals: int = Field(default=18, ge=0, le=18)
    initial_supply: str = Field(default="1000000")
    max_supply: str | None = None
    mintable: bool = True
    burnable: bool = True
    pausable: bool = False
    owner_address: str | None = None
    metadata_uri: str | None = None
    # Blacklist / anti-bot
    blacklist_enabled: bool = True
    blacklist_addresses: list[str] = Field(default_factory=list)
    max_wallet_limit: str | None = None
    max_tx_limit: str | None = None
    trading_enabled: bool = True
    anti_bot_delay: int = 0
    # Advanced
    tax_rate: float | None = None
    tax_recipient: str | None = None
    deflationary: bool = False


class MintRequest(BaseModel):
    deployment_id: str
    to_address: str
    amount: str


class BurnRequest(BaseModel):
    deployment_id: str
    amount: str


class TransferOwnershipRequest(BaseModel):
    deployment_id: str
    new_owner: str


class BlacklistRequest(BaseModel):
    deployment_id: str
    address: str


class SetLimitRequest(BaseModel):
    deployment_id: str
    amount: str


class TradingToggleRequest(BaseModel):
    deployment_id: str
    enabled: bool


# ── Endpoints ─────────────────────────────────────────────────


@router.get("/chains")
async def list_chains(request: Request, _=Depends(_verify_admin)):
    """List all supported chains and their configuration status."""
    chains = TokenDeployerFactory.list_supported_chains()
    return {
        "chains": chains,
        "total": len(chains),
        "configured": sum(1 for c in chains if c["configured"]),
    }


@router.post("/deploy")
async def deploy_token(request: Request, body: DeployTokenRequest, _=Depends(_verify_admin)):
    """Deploy a new token on the specified chain."""
    try:
        # Get deployer for chain
        deployer = TokenDeployerFactory.get_deployer(body.chain)

        # Build params
        params = DeployParams(
            chain=body.chain,
            name=body.name,
            symbol=body.symbol,
            decimals=body.decimals,
            initial_supply=body.initial_supply,
            max_supply=body.max_supply,
            mintable=body.mintable,
            burnable=body.burnable,
            pausable=body.pausable,
            owner_address=body.owner_address or deployer.deployer_address,
            metadata_uri=body.metadata_uri,
            blacklist_enabled=body.blacklist_enabled,
            blacklist_addresses=body.blacklist_addresses,
            max_wallet_limit=body.max_wallet_limit,
            max_tx_limit=body.max_tx_limit,
            trading_enabled=body.trading_enabled,
            anti_bot_delay=body.anti_bot_delay,
            tax_rate=body.tax_rate,
            tax_recipient=body.tax_recipient,
            deflationary=body.deflationary,
        )

        # Deploy
        deployment = await deployer.deploy_token(params)

        # Apply initial blacklist if provided
        if body.blacklist_addresses and body.blacklist_enabled:
            for addr in body.blacklist_addresses:
                try:
                    await deployer.blacklist_add(deployment.contract_address, addr)
                    logger.info(f"Blacklisted {addr} on {deployment.contract_address}")
                except Exception as e:
                    logger.warning(f"Failed to blacklist {addr}: {e}")

        # Apply max limits if set
        if body.max_wallet_limit:
            try:
                await deployer.set_max_wallet(deployment.contract_address, body.max_wallet_limit)
            except Exception as e:
                logger.warning(f"Failed to set max wallet: {e}")

        if body.max_tx_limit:
            try:
                await deployer.set_max_tx(deployment.contract_address, body.max_tx_limit)
            except Exception as e:
                logger.warning(f"Failed to set max tx: {e}")

        # Save deployment record
        storage = await get_storage()
        await storage.save(deployment)

        return {
            "success": True,
            "deployment": deployment.to_dict(),
            "explorer_url": _get_explorer_url(body.chain, deployment.contract_address),
        }

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Token deployment failed: {e}")
        raise HTTPException(status_code=500, detail=f"Deployment failed: {e!s}") from e


@router.post("/mint")
async def mint_tokens(request: Request, body: MintRequest, _=Depends(_verify_admin)):
    """Mint additional tokens for a deployed contract."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        if deployment.status != "deployed":
            raise HTTPException(status_code=400, detail=f"Cannot mint - status is {deployment.status}")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.mint_tokens(deployment.contract_address, body.to_address, body.amount)

        await storage.update_status(body.deployment_id, "minting", {"last_mint_tx": tx_hash})

        return {
            "success": True,
            "tx_hash": tx_hash,
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Mint failed: {e}")
        raise HTTPException(status_code=500, detail=f"Mint failed: {e!s}") from e


@router.post("/burn")
async def burn_tokens(request: Request, body: BurnRequest, _=Depends(_verify_admin)):
    """Burn tokens from deployer balance."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.burn_tokens(deployment.contract_address, body.amount)

        await storage.update_status(body.deployment_id, "burned", {"last_burn_tx": tx_hash})

        return {
            "success": True,
            "tx_hash": tx_hash,
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Burn failed: {e}")
        raise HTTPException(status_code=500, detail=f"Burn failed: {e!s}") from e


@router.post("/transfer")
async def transfer_ownership(request: Request, body: TransferOwnershipRequest, _=Depends(_verify_admin)):
    """Transfer contract ownership to a new address."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.transfer_ownership(deployment.contract_address, body.new_owner)

        await storage.update_status(
            body.deployment_id,
            "ownership_transferred",
            {"new_owner": body.new_owner, "transfer_tx": tx_hash},
        )

        return {
            "success": True,
            "tx_hash": tx_hash,
            "new_owner": body.new_owner,
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ownership transfer failed: {e}")
        raise HTTPException(status_code=500, detail=f"Transfer failed: {e!s}") from e


@router.post("/renounce")
async def renounce_ownership(request: Request, body: dict = Body(...), _=Depends(_verify_admin)):
    """Renounce contract ownership (make immutable). WARNING: Irreversible."""
    deployment_id = body.get("deployment_id", "")

    try:
        storage = await get_storage()
        deployment = await storage.get(deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.renounce_ownership(deployment.contract_address)

        await storage.update_status(deployment_id, "ownership_renounced", {"renounce_tx": tx_hash})

        return {
            "success": True,
            "tx_hash": tx_hash,
            "warning": "Ownership renounced. Contract is now immutable.",
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Renounce failed: {e}")
        raise HTTPException(status_code=500, detail=f"Renounce failed: {e!s}") from e


# ── Blacklist / Anti-Bot Endpoints ────────────────────────────


@router.post("/blacklist")
async def blacklist_add(request: Request, body: BlacklistRequest, _=Depends(_verify_admin)):
    """Add an address to the token blacklist."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.blacklist_add(deployment.contract_address, body.address)

        return {
            "success": True,
            "tx_hash": tx_hash,
            "address": body.address,
            "action": "blacklisted",
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Blacklist add failed: {e}")
        raise HTTPException(status_code=500, detail=f"Blacklist failed: {e!s}") from e


@router.post("/unblacklist")
async def blacklist_remove(request: Request, body: BlacklistRequest, _=Depends(_verify_admin)):
    """Remove an address from the token blacklist."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.blacklist_remove(deployment.contract_address, body.address)

        return {
            "success": True,
            "tx_hash": tx_hash,
            "address": body.address,
            "action": "unblacklisted",
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unblacklist failed: {e}")
        raise HTTPException(status_code=500, detail=f"Unblacklist failed: {e!s}") from e


@router.get("/blacklist/check")
async def check_blacklist(request: Request, deployment_id: str, address: str, _=Depends(_verify_admin)):
    """Check if an address is blacklisted."""
    try:
        storage = await get_storage()
        deployment = await storage.get(deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        is_blacklisted = await deployer.is_blacklisted(deployment.contract_address, address)

        return {
            "address": address,
            "is_blacklisted": is_blacklisted,
            "contract": deployment.contract_address,
            "chain": deployment.chain,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Blacklist check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Check failed: {e!s}") from e


@router.post("/trading")
async def set_trading(request: Request, body: TradingToggleRequest, _=Depends(_verify_admin)):
    """Enable or disable trading for the token."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.set_trading_enabled(deployment.contract_address, body.enabled)

        return {
            "success": True,
            "tx_hash": tx_hash,
            "trading_enabled": body.enabled,
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Trading toggle failed: {e}")
        raise HTTPException(status_code=500, detail=f"Toggle failed: {e!s}") from e


@router.post("/max-wallet")
async def set_max_wallet(request: Request, body: SetLimitRequest, _=Depends(_verify_admin)):
    """Set maximum wallet holding limit."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.set_max_wallet(deployment.contract_address, body.amount)

        return {
            "success": True,
            "tx_hash": tx_hash,
            "max_wallet": body.amount,
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Max wallet set failed: {e}")
        raise HTTPException(status_code=500, detail=f"Set failed: {e!s}") from e


@router.post("/max-tx")
async def set_max_tx(request: Request, body: SetLimitRequest, _=Depends(_verify_admin)):
    """Set maximum transaction limit."""
    try:
        storage = await get_storage()
        deployment = await storage.get(body.deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        tx_hash = await deployer.set_max_tx(deployment.contract_address, body.amount)

        return {
            "success": True,
            "tx_hash": tx_hash,
            "max_tx": body.amount,
            "explorer_url": _get_explorer_url(deployment.chain, tx_hash, is_tx=True),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Max tx set failed: {e}")
        raise HTTPException(status_code=500, detail=f"Set failed: {e!s}") from e


# ── Query Endpoints ───────────────────────────────────────────


@router.get("/list")
async def list_deployments(request: Request, chain: str | None = None, limit: int = 100, _=Depends(_verify_admin)):
    """List all token deployments."""
    try:
        storage = await get_storage()
        deployments = await storage.list_all(chain=chain, limit=limit)

        return {
            "deployments": [d.to_dict() for d in deployments],
            "total": len(deployments),
            "chain_filter": chain,
        }

    except Exception as e:
        logger.error(f"List failed: {e}")
        raise HTTPException(status_code=500, detail=f"List failed: {e!s}") from e


@router.get("/{deployment_id}")
async def get_deployment(request: Request, deployment_id: str, _=Depends(_verify_admin)):
    """Get deployment details by ID."""
    try:
        storage = await get_storage()
        deployment = await storage.get(deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        # Get on-chain info
        try:
            deployer = TokenDeployerFactory.get_deployer(deployment.chain)
            token_info = await deployer.get_token_info(deployment.contract_address)
        except Exception:
            token_info = {}

        return {
            "deployment": deployment.to_dict(),
            "on_chain_info": token_info,
            "explorer_url": _get_explorer_url(deployment.chain, deployment.contract_address),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get deployment failed: {e}")
        raise HTTPException(status_code=500, detail=f"Get failed: {e!s}") from e


@router.get("/{deployment_id}/balance/{wallet_address}")
async def get_balance(request: Request, deployment_id: str, wallet_address: str, _=Depends(_verify_admin)):
    """Get token balance for a specific wallet."""
    try:
        storage = await get_storage()
        deployment = await storage.get(deployment_id)
        if not deployment:
            raise HTTPException(status_code=404, detail="Deployment not found")

        deployer = TokenDeployerFactory.get_deployer(deployment.chain)
        balance = await deployer.get_balance(deployment.contract_address, wallet_address)

        return {
            "wallet": wallet_address,
            "balance": balance,
            "contract": deployment.contract_address,
            "chain": deployment.chain,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Balance check failed: {e}")
        raise HTTPException(status_code=500, detail=f"Balance check failed: {e!s}") from e


# ── Helpers ───────────────────────────────────────────────────


def _get_explorer_url(chain: str, address_or_tx: str, is_tx: bool = False) -> str:
    """Get block explorer URL for a chain."""
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
