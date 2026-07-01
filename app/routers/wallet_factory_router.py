"""
RMI Wallet Factory API — Multi-Chain Wallet Generation & Rotation
==================================================================
REST API for generating, rotating, and managing wallets across 25+ blockchains.
Secure key storage with AES-256-GCM encryption.

Endpoints:
    GET  /api/v1/wallets/chains          — List supported chains
    POST /api/v1/wallets/generate        — Generate wallet(s)
    POST /api/v1/wallets/generate/batch  — Generate for multiple chains
    POST /api/v1/wallets/rotate          — Rotate to new wallet
    GET  /api/v1/wallets/vault           — List vault wallets (no keys)
    GET  /api/v1/wallets/vault/{id}      — Get wallet with key (auth required)
    GET  /api/v1/wallets/stats           — Wallet factory statistics
    POST /api/v1/wallets/export          — Export for Apify/CLI usage

Free version: 3 chains, no rotation, no encryption
Full version: All 25+ chains, rotation, encryption — available on Apify
"""

import json
import logging
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger("wallet_api")

router = APIRouter(prefix="/api/v1/chain-vault", tags=["Chain Vault"])

# ── Import wallet factory ───────────────────────────────────────
from app.wallet_factory import SUPPORTED_CHAINS, get_wallet_factory

# ── Auth ────────────────────────────────────────────────────────
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "")


def _check_auth(request: Request) -> bool:
    """Verify admin API key for sensitive operations."""
    auth = request.headers.get("X-API-Key", "") or request.headers.get("Authorization", "").replace("Bearer ", "")
    return auth == ADMIN_KEY if ADMIN_KEY else True  # Allow if no admin key set


# ── Models ──────────────────────────────────────────────────────


class GenerateRequest(BaseModel):
    chain: str = Field(..., description="Chain key (btc, eth, sol, trx, etc.)")
    label: str = Field(default="", description="Optional wallet label")
    tags: list[str] = Field(default_factory=list, description="Optional tags")
    count: int = Field(default=1, ge=1, le=10, description="Number of wallets to generate")


class BatchGenerateRequest(BaseModel):
    chains: list[str] = Field(..., description="List of chain keys")
    label_prefix: str = Field(default="", description="Label prefix for all wallets")


class RotateRequest(BaseModel):
    chain: str = Field(..., description="Chain key to rotate")


class ExportRequest(BaseModel):
    chains: list[str] = Field(default=[], description="Chains to export (empty = all)")
    format: str = Field(default="json", description="Export format: json, csv, env")


# ── Public Endpoints ────────────────────────────────────────────


@router.get("/chains")
async def list_chains():
    """List all supported blockchain networks with metadata.

    Free tier: 3 chains shown. Full version: 25+ chains.
    """
    chains = {}
    for key, cfg in SUPPORTED_CHAINS.items():
        chains[key] = {
            "name": cfg.name,
            "symbol": cfg.symbol,
            "family": cfg.family.value,
            "description": cfg.description,
            "hd_path": cfg.hd_path,
            "slip44": cfg.slip44,
        }
    return {
        "total_chains": len(chains),
        "chain_families": len({c["family"] for c in chains.values()}),
        "chains": chains,
        "pricing": {
            "free": "3 chains (btc, eth, sol) — basic generation only",
            "full": "25+ chains, rotation, encrypted vault, batch generation — available on Apify",
        },
    }


@router.get("/stats")
async def wallet_stats():
    """Get wallet factory statistics and health."""
    factory = get_wallet_factory()
    return factory.stats


@router.get("/vault")
async def list_vault(request: Request):
    """List all wallets in the vault (no private keys exposed)."""
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Admin API key required")

    vault_path = "/root/.rmi/wallets/vault.json"
    if not os.path.exists(vault_path):
        return {"wallets": [], "total": 0, "message": "Vault is empty"}

    with open(vault_path) as f:
        vault = json.load(f)

    wallets = []
    for key, data in vault.items():
        if key == "_meta":
            continue
        wallets.append(
            {
                "id": key,
                "chain": data.get("chain"),
                "chain_name": data.get("chain_name"),
                "address": data.get("address"),
                "label": data.get("label"),
                "tags": data.get("tags", []),
                "created_at": data.get("created_at"),
                "has_private_key": bool(data.get("private_key_hex")),
                "encrypted": data.get("encrypted", False),
            }
        )

    return {
        "wallets": wallets,
        "total": len(wallets),
        "meta": vault.get("_meta", {}),
    }


@router.get("/vault/{wallet_id}")
async def get_wallet(wallet_id: str, request: Request):
    """Get full wallet details INCLUDING private key (admin auth required)."""
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Admin API key required")

    vault_path = "/root/.rmi/wallets/vault.json"
    if not os.path.exists(vault_path):
        raise HTTPException(status_code=404, detail="Vault not found")

    with open(vault_path) as f:
        vault = json.load(f)

    if wallet_id not in vault:
        raise HTTPException(status_code=404, detail=f"Wallet {wallet_id} not found")

    wallet = vault[wallet_id]

    # Decrypt if needed
    if wallet.get("encrypted"):
        factory = get_wallet_factory()
        try:
            wallet["private_key_hex"] = factory.decrypt_key(wallet["private_key_hex"])
            wallet["decrypted"] = True
        except Exception as e:
            wallet["decrypted"] = False
            wallet["decrypt_error"] = str(e)

    return wallet


# ── Generation Endpoints ────────────────────────────────────────


@router.post("/generate")
async def generate_wallet(req: GenerateRequest, request: Request):
    """Generate a new wallet for any supported chain.

    Free tier: btc, eth, sol only. Full version: all 25+ chains.
    Returns address + public key only. Private key stored in vault.
    """
    chain = req.chain.lower()
    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported chain: {chain}. Use GET /chains to see supported chains.",
        )

    factory = get_wallet_factory()
    wallets = []

    for i in range(req.count):
        label = req.label or f"{chain}_wallet_{i + 1}"
        wallet = factory.generate(chain, label=label, tags=[*req.tags, f"generated_{i + 1}"])
        factory.save_to_vault(wallet)
        wallets.append(wallet.to_safe_dict())

    return {
        "generated": len(wallets),
        "chain": chain,
        "chain_name": SUPPORTED_CHAINS[chain].name,
        "wallets": wallets,
        "vault_location": "/root/.rmi/wallets/vault.json",
        "note": "Private keys stored securely. Use GET /vault/{id} with admin key to retrieve.",
    }


@router.post("/generate/batch")
async def generate_batch(req: BatchGenerateRequest, request: Request):
    """Generate wallets for multiple chains in one request."""
    invalid = [c for c in req.chains if c.lower() not in SUPPORTED_CHAINS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported chains: {invalid}")

    factory = get_wallet_factory()
    results = {}

    for chain in req.chains:
        wallet = factory.generate(chain, label=f"{req.label_prefix}_{chain}" if req.label_prefix else chain)
        factory.save_to_vault(wallet)
        results[chain] = wallet.to_safe_dict()

    return {
        "chains_generated": len(results),
        "wallets": results,
        "vault_location": "/root/.rmi/wallets/vault.json",
    }


# ── Rotation ────────────────────────────────────────────────────


@router.post("/rotate")
async def rotate_wallet(req: RotateRequest, request: Request):
    """Rotate to a new wallet for a chain. Old wallet remains in vault.

    Generates a fresh wallet and marks it as the active rotation target.
    Perfect for security-conscious payment collection.
    """
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Admin API key required")

    chain = req.chain.lower()
    if chain not in SUPPORTED_CHAINS:
        raise HTTPException(status_code=400, detail=f"Unsupported chain: {chain}")

    factory = get_wallet_factory()
    wallet = factory.rotate(chain)

    return {
        "rotated": True,
        "chain": chain,
        "new_address": wallet.address,
        "rotation_index": factory._rotation_index.get(chain, 1),
        "message": f"New {chain.upper()} wallet active. Old wallet preserved in vault.",
    }


# ── Export (Apify-ready) ────────────────────────────────────────


@router.post("/export")
async def export_wallets(req: ExportRequest, request: Request):
    """Export wallet data for Apify integration or CLI usage.

    Formats: json (full), csv (addresses only), env (KEY=VALUE pairs).
    Free version: 3 chains only.
    """
    if not _check_auth(request):
        raise HTTPException(status_code=401, detail="Admin API key required")

    chains = req.chains or list(SUPPORTED_CHAINS.keys())
    chains = [c for c in chains if c in SUPPORTED_CHAINS]

    factory = get_wallet_factory()
    wallets = {}
    for c in chains:
        wallet = factory.generate(c)
        wallets[c] = wallet

    if req.format == "csv":
        lines = ["chain,address,label,symbol"]
        for c, w in wallets.items():
            lines.append(f"{c},{w.address},{w.label},{w.symbol}")
        return {"format": "csv", "data": "\n".join(lines)}

    elif req.format == "env":
        lines = [f"# RMI Wallet Export — {len(wallets)} chains"]
        for c, w in wallets.items():
            lines.append(f"WALLET_{c.upper()}_ADDRESS={w.address}")
        return {"format": "env", "data": "\n".join(lines)}

    else:
        return {
            "format": "json",
            "total_wallets": len(wallets),
            "wallets": {c: w.to_safe_dict() for c, w in wallets.items()},
            "apify_link": "https://apify.com/rugmunch/wallet-factory",
        }
