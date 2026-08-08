"""
Web3 Chat Router - Token & NFT Gate Routes
=============================================
Token-gated and NFT-gated room management: set, get, remove, and verify gates.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user, require_auth
from app.routers.web3_chat.models import (  # noqa: TC001 — Pydantic request models, needs runtime resolution
    NFTGateRequest,
    TokenGateRequest,
    VerifyGateRequest,
)
from app.services.web3_chat import (
    GateType,
    TokenGate,
    get_store,
    verify_min_value_holdings,
    verify_nft_holding,
    verify_token_holdings,
)

router = APIRouter()


# ── Token-Gated Rooms ────────────────────────────────────────────


@router.post("/rooms/{room_id}/token-gate")
async def set_token_gate(
    room_id: str,
    req: TokenGateRequest,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Set token gate requirements for a room."""
    if not req.min_amount or Decimal(req.min_amount) <= 0:
        raise HTTPException(status_code=400, detail="Minimum amount must be positive")

    gate = TokenGate(
        room_id=room_id,
        gate_type=GateType.TOKEN,
        token_address=req.token_address,
        token_symbol=req.token_symbol,
        chain=req.chain,
        min_amount=req.min_amount,
        enabled=req.enabled,
    )
    get_store().save_token_gate(gate)

    return {
        "room_id": room_id,
        "gate_type": "token",
        "token_symbol": req.token_symbol or req.token_address,
        "min_amount": req.min_amount,
        "chain": req.chain,
        "enabled": req.enabled,
        "requirement_description": f"Hold {req.min_amount} {req.token_symbol or 'tokens'} to join",
    }


@router.get("/rooms/{room_id}/token-gate")
async def get_token_gate(
    room_id: str,
    user: dict[str, Any] | None = Depends(get_current_user),
) -> dict[str, Any]:
    """Get token gate requirements for a room."""
    gate = get_store().get_token_gate(room_id)
    if not gate:
        return {"room_id": room_id, "gated": False}

    return {
        "room_id": room_id,
        "gated": True,
        "gate_type": "token",
        "token_address": gate.token_address,
        "token_symbol": gate.token_symbol,
        "chain": gate.chain,
        "min_amount": gate.min_amount,
        "enabled": gate.enabled,
        "requirement": f"Hold {gate.min_amount} {gate.token_symbol or 'tokens'}",
    }


@router.delete("/rooms/{room_id}/token-gate")
async def remove_token_gate(
    room_id: str,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Remove token gate from a room."""
    get_store().remove_gate(room_id)
    return {"room_id": room_id, "gated": False, "status": "removed"}


@router.post("/rooms/{room_id}/verify-gate")
async def verify_room_gate(
    room_id: str,
    req: VerifyGateRequest,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Verify a wallet meets token gate requirements."""
    gate = get_store().get_token_gate(room_id) or get_store().get_nft_gate(room_id)
    if not gate:
        return {"room_id": room_id, "gated": False, "verified": True}

    chain = req.chain or gate.chain or "ethereum"

    if gate.gate_type == GateType.TOKEN and gate.token_address:
        result = await verify_token_holdings(
            address=req.address,
            token_address=gate.token_address,
            chain=chain,
            min_amount=gate.min_amount or "0",
        )
    elif gate.gate_type == GateType.MIN_VALUE:
        result = await verify_min_value_holdings(
            address=req.address,
            chain=chain,
            min_usd=gate.min_amount or "0",
        )
    elif gate.gate_type == GateType.NFT or gate.gate_type == GateType.ANY_NFT:
        result = await verify_nft_holding(
            address=req.address,
            chain=chain,
            contract=gate.token_address if gate.gate_type == GateType.NFT else None,
            collection_name=gate.nft_collection_name,
            creator=gate.nft_creator,
        )
    else:
        result = {"verified": False, "error": "Unknown gate type"}

    return {
        "room_id": room_id,
        "gated": True,
        "verified": result.get("verified", False),
        "details": result,
    }


# ── NFT-Gated Rooms ────────────────────────────────────────────


@router.post("/rooms/{room_id}/nft-gate")
async def set_nft_gate(
    room_id: str,
    req: NFTGateRequest,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Set NFT gate requirements for a room."""
    if not req.contract_address and not req.collection_name and not req.creator_address:
        # "any NFT" mode
        gate = TokenGate(
            room_id=room_id,
            gate_type=GateType.ANY_NFT,
            chain=req.chain,
            enabled=True,
        )
    else:
        gate = TokenGate(
            room_id=room_id,
            gate_type=GateType.NFT,
            token_address=req.contract_address,
            nft_collection_name=req.collection_name,
            nft_creator=req.creator_address,
            chain=req.chain,
            enabled=True,
        )

    get_store().save_nft_gate(gate)

    # Build description
    if gate.gate_type == GateType.ANY_NFT:
        desc = "Hold any NFT to join this room"
    elif req.collection_name:
        desc = f"Hold a {req.collection_name} NFT to join this room"
    elif req.contract_address:
        desc = "Hold an NFT from the specified collection to join"
    elif req.creator_address:
        desc = f"Hold any NFT created by {req.creator_address} to join"
    else:
        desc = "NFT required to join"

    return {
        "room_id": room_id,
        "gate_type": "nft",
        "contract_address": req.contract_address,
        "collection_name": req.collection_name,
        "creator_address": req.creator_address,
        "chain": req.chain,
        "enabled": True,
        "requirement_description": desc,
    }


@router.get("/rooms/{room_id}/nft-gate")
async def get_nft_gate(
    room_id: str,
    user: dict[str, Any] | None = Depends(get_current_user),
) -> dict[str, Any]:
    """Get NFT gate requirements for a room."""
    gate = get_store().get_nft_gate(room_id)
    if not gate:
        return {"room_id": room_id, "gated": False}

    return {
        "room_id": room_id,
        "gated": True,
        "gate_type": "nft",
        "chain": gate.chain,
        "contract_address": gate.token_address,
        "collection_name": gate.nft_collection_name,
        "creator": gate.nft_creator,
        "enabled": gate.enabled,
    }
