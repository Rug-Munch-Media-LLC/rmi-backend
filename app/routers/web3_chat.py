"""
Web3 Chat Router
================
Web3-native chat features:
- /pay, /tip, /split, /request — send crypto in chat
- Wallet connect/disconnect, balances, swap
- Token-gated rooms — require minimum token holdings
- NFT-gated rooms — require specific NFTs
- Transaction history, gas estimation, payment notifications

All routes: /api/v1/chat/web3/*
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.auth import get_current_user, require_auth
from app.core.logging import get_logger
from app.services.web3_chat import (
    GateType,
    PaymentRequest,
    PaymentStatus,
    SplitPayment,
    TipRecord,
    TokenGate,
    estimate_gas,
    format_tx_link,
    generate_nonce,
    get_store,
    get_token_price,
    get_wallet_balance,
    parse_amount,
    simulate_payment,
    validate_address,
    verify_min_value_holdings,
    verify_nft_holding,
    verify_payment_command,
    verify_token_holdings,
)

logger = get_logger("web3_chat_router")

router = APIRouter(prefix="/api/v1/chat/web3", tags=["chat-web3"])

# ── Pydantic Models ───────────────────────────────────────────────


class PayRequest(BaseModel):
    recipient: str = Field(..., description="Recipient username or wallet address")
    amount: str = Field(..., description="Amount to send")
    token: str = Field(default="ETH", description="Token symbol: ETH, SOL, USDC, USDT, DAI, BTC")
    chain: str = Field(
        default="ethereum", description="Blockchain: ethereum, solana, base, polygon, optimism, arbitrum, lightning"
    )
    room_id: str | None = Field(default=None, description="Room context for the payment")
    message: str | None = Field(default=None, max_length=200, description="Optional payment message")
    wallet_address: str = Field(..., description="Sender wallet address")

    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        token = v.upper()
        valid_tokens = {"ETH", "SOL", "USDC", "USDT", "DAI", "BTC"}
        if token not in valid_tokens:
            raise ValueError(f"Unsupported token: {token}. Supported: {', '.join(valid_tokens)}")
        return token

    @field_validator("chain")
    @classmethod
    def validate_chain(cls, v: str) -> str:
        chain = v.lower()
        valid_chains = {"ethereum", "solana", "base", "polygon", "optimism", "arbitrum", "lightning"}
        if chain not in valid_chains:
            raise ValueError(f"Unsupported chain: {chain}")
        return chain


class TipRequest(BaseModel):
    recipient: str = Field(..., description="Recipient username")
    amount: str = Field(..., description="Tip amount")
    token: str = Field(default="ETH")
    chain: str = Field(default="ethereum")
    room_id: str | None = None
    message: str | None = Field(default=None, max_length=200)


class SplitRequest(BaseModel):
    recipients: list[str] = Field(..., min_length=1, max_length=50, description="Recipient usernames")
    total_amount: str = Field(..., description="Total amount to split")
    token: str = Field(default="ETH")
    chain: str = Field(default="ethereum")
    room_id: str | None = None


class RequestPayment(BaseModel):
    recipient: str = Field(..., description="User to request payment from")
    amount: str = Field(..., description="Amount to request")
    token: str = Field(default="ETH")
    chain: str = Field(default="ethereum")
    reason: str | None = Field(default=None, max_length=300, description="Reason for payment request")
    room_id: str | None = None


class ConnectWalletRequest(BaseModel):
    address: str = Field(..., description="Wallet address")
    chain: str = Field(default="ethereum")
    signature: str | None = Field(default=None, description="Signed message for verification")
    message: str | None = Field(default=None, description="Message that was signed")
    wallet_type: str = Field(default="evm", description="wallet type: metamask, phantom, rainbow, walletconnect, okx")


class TokenGateRequest(BaseModel):
    token_address: str | None = Field(default=None, description="Token contract/mint address")
    token_symbol: str | None = Field(default=None, description="Token symbol (e.g., $TICKER)")
    chain: str = Field(default="ethereum")
    min_amount: str = Field(..., description="Minimum token amount required")
    enabled: bool = True


class NFTGateRequest(BaseModel):
    contract_address: str | None = Field(default=None, description="NFT contract address (any = any NFT)")
    collection_name: str | None = Field(default=None, description="Collection name for Solana/verification")
    creator_address: str | None = Field(default=None, description="NFT creator address")
    chain: str = Field(default="ethereum")


class SwapQuoteRequest(BaseModel):
    from_token: str = Field(..., description="Token to swap from")
    to_token: str = Field(..., description="Token to swap to")
    amount: str = Field(..., description="Amount to swap")
    chain: str = Field(default="ethereum")
    wallet_address: str = Field(..., description="Wallet address for quote")


class VerifyGateRequest(BaseModel):
    address: str = Field(..., description="Wallet address to verify")
    chain: str | None = None


# ── Helper: Get validated store ──


def _store() -> Any:
    return get_store()


def _make_tx_message(
    payment_type: str,
    from_user: str,
    to_user: str,
    amount: str,
    token: str,
    tx_hash: str | None = None,
    chain: str = "ethereum",
) -> dict[str, Any]:
    """Build a structured chat message for a transaction."""
    msg: dict[str, Any] = {
        "type": "transaction",
        "transaction_type": payment_type,
        "from": from_user,
        "to": to_user,
        "amount": amount,
        "token": token,
        "chain": chain,
        "timestamp": datetime.now(UTC).isoformat(),
        "tx_hash": tx_hash,
        "tx_link": format_tx_link(tx_hash, chain) if tx_hash else None,
        "status": "confirmed" if tx_hash else "pending",
    }
    return msg


# ── Routes ────────────────────────────────────────────────────────


@router.post("/pay")
async def chat_pay(
    req: PayRequest,
    request: Request,
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Send a payment in chat. Requires wallet confirmation.

    Validates the payment, provides gas estimation, and returns
    a payment request object for the frontend to confirm/sign.
    """
    user_id = user.get("id", "unknown")
    sender_display = user.get("display_name", user.get("email", user_id))

    # Validate recipient
    if not req.recipient or len(req.recipient.strip()) < 1:
        raise HTTPException(status_code=400, detail="Recipient is required")
    recipient = req.recipient.strip()

    # Validate amount
    try:
        amount_dec = parse_amount(req.amount)
        if amount_dec <= 0:
            raise HTTPException(status_code=400, detail="Amount must be positive")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    # Validate wallet
    if not validate_address(req.wallet_address, req.chain):
        raise HTTPException(status_code=400, detail=f"Invalid wallet address for {req.chain} chain")

    # Simulate payment to get gas estimation and feasibility
    sim = await simulate_payment(
        from_addr=req.wallet_address,
        to_addr=recipient if recipient.startswith("0x") else req.wallet_address,
        amount=req.amount,
        token=req.token,
        chain=req.chain,
    )
    if not sim.get("valid"):
        raise HTTPException(status_code=400, detail=sim.get("error", "Payment validation failed"))

    # Create payment record
    payment = PaymentRequest(
        sender_id=user_id,
        recipient_username=recipient,
        amount=req.amount,
        token=req.token,
        chain=req.chain,
        room_id=req.room_id,
        gas_estimation=sim.get("gas_estimation"),
    )
    _store().save_payment(payment)

    # Build structured message for chat display
    chat_message = _make_tx_message("pay", sender_display, recipient, req.amount, req.token, chain=req.chain)

    # Get USD value
    price = await get_token_price(req.token)
    usd_value = amount_dec * price

    return {
        "id": payment.id,
        "status": "pending_confirmation",
        "payment": {
            "id": payment.id,
            "from": sender_display,
            "to": recipient,
            "amount": req.amount,
            "token": req.token,
            "chain": req.chain,
            "usd_value": str(usd_value),
            "gas_estimation": sim.get("gas_estimation"),
        },
        "message": req.message,
        "chat_message": chat_message,
        "requires_signature": True,
        "instructions": f"Please confirm sending {req.amount} {req.token} to {recipient} in your wallet",
    }


@router.post("/pay/confirm")
async def confirm_payment(
    payment_id: str = Query(..., description="Payment ID to confirm"),
    tx_hash: str = Query(..., description="Transaction hash from wallet"),
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Confirm a payment with a transaction hash after wallet signing."""
    store = _store()
    payment = store.get_payment(payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    if not tx_hash or len(tx_hash) < 10:
        raise HTTPException(status_code=400, detail="Invalid transaction hash")

    store.update_payment_status(payment_id, PaymentStatus.CONFIRMED, tx_hash)

    return {
        "id": payment_id,
        "status": "confirmed",
        "tx_hash": tx_hash,
        "tx_link": format_tx_link(tx_hash, payment.chain),
        "chat_message": _make_tx_message(
            "pay_confirmed",
            payment.sender_id,
            payment.recipient_username,
            payment.amount,
            payment.token,
            tx_hash,
            payment.chain,
        ),
    }


@router.post("/tip")
async def chat_tip(
    req: TipRequest,
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Tip a user in any room."""
    user_id = user.get("id", "unknown")
    sender_display = user.get("display_name", user.get("email", user_id))

    # Validate
    if not req.recipient:
        raise HTTPException(status_code=400, detail="Recipient required")
    try:
        amount = parse_amount(req.amount)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Tip must be positive")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    tip = TipRecord(
        id=f"tip-{uuid.uuid4().hex[:12]}",
        from_user=user_id,
        to_user=req.recipient,
        amount=req.amount,
        token=req.token.upper(),
        chain=req.chain,
        room_id=req.room_id,
        message=req.message,
        tx_hash=None,
        status=PaymentStatus.PENDING,
        created_at=datetime.now(UTC).isoformat(),
    )
    _store().save_tip(tip)

    return {
        "id": tip.id,
        "status": "pending",
        "tip": {
            "from": sender_display,
            "to": req.recipient,
            "amount": req.amount,
            "token": req.token.upper(),
            "chain": req.chain,
        },
        "chat_message": _make_tx_message(
            "tip",
            sender_display,
            req.recipient,
            req.amount,
            req.token.upper(),
            chain=req.chain,
        ),
        "message": req.message,
    }


@router.post("/request")
async def request_payment(
    req: RequestPayment,
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Request a payment from another user."""
    user_id = user.get("id", "unknown")
    sender_display = user.get("display_name", user.get("email", user_id))

    if not req.recipient:
        raise HTTPException(status_code=400, detail="Recipient required")

    request_id = f"req-{uuid.uuid4().hex[:12]}"

    return {
        "id": request_id,
        "type": "payment_request",
        "from": sender_display,
        "to": req.recipient,
        "amount": req.amount,
        "token": req.token.upper(),
        "chain": req.chain,
        "reason": req.reason,
        "status": "pending",
        "chat_message": {
            "type": "payment_request",
            "from": sender_display,
            "to": req.recipient,
            "amount": req.amount,
            "token": req.token.upper(),
            "chain": req.chain,
            "reason": req.reason,
            "request_id": request_id,
        },
    }


@router.post("/split")
async def split_payment(
    req: SplitRequest,
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Split a payment among multiple recipients in a group chat."""
    user_id = user.get("id", "unknown")
    sender_display = user.get("display_name", user.get("email", user_id))

    if len(req.recipients) < 1:
        raise HTTPException(status_code=400, detail="At least one recipient required")

    try:
        total = parse_amount(req.total_amount)
        if total <= 0:
            raise HTTPException(status_code=400, detail="Total amount must be positive")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    split_amount = total / Decimal(str(len(req.recipients)))

    split = SplitPayment(
        id=f"split-{uuid.uuid4().hex[:12]}",
        initiator=user_id,
        recipients=req.recipients,
        total_amount=req.total_amount,
        token=req.token.upper(),
        chain=req.chain,
        room_id=req.room_id,
        tx_hash=None,
        status=PaymentStatus.PENDING,
        created_at=datetime.now(UTC).isoformat(),
    )
    _store().save_split(split)

    return {
        "id": split.id,
        "type": "split_payment",
        "from": sender_display,
        "recipients": req.recipients,
        "total_amount": req.total_amount,
        "split_amount": str(split_amount),
        "token": req.token.upper(),
        "chain": req.chain,
        "status": "pending",
        "chat_message": {
            "type": "split_payment",
            "initiator": sender_display,
            "recipients": req.recipients,
            "total_amount": req.total_amount,
            "split_amount": str(split_amount),
            "token": req.token.upper(),
            "per_person": f"{split_amount} {req.token.upper()} each",
        },
    }


@router.get("/transactions")
async def transaction_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Get user's transaction history (payments, tips, splits)."""
    user_id = user.get("id")
    store = _store()

    payments = store.get_user_payments(user_id)
    tips = store.get_user_tips(user_id)

    # Combine and sort by timestamp
    all_txns: list[dict[str, Any]] = []

    for p in payments:
        all_txns.append(
            {
                "id": p.id,
                "type": "payment",
                "sender": p.sender_id,
                "recipient": p.recipient_username,
                "amount": p.amount,
                "token": p.token,
                "chain": p.chain,
                "tx_hash": p.tx_hash,
                "tx_link": format_tx_link(p.tx_hash, p.chain) if p.tx_hash else None,
                "status": p.status.value,
                "created_at": p.created_at,
            }
        )

    for t in tips:
        all_txns.append(
            {
                "id": t.id,
                "type": "tip",
                "from": t.from_user,
                "to": t.to_user,
                "amount": t.amount,
                "token": t.token,
                "chain": t.chain,
                "tx_hash": t.tx_hash,
                "status": t.status.value,
                "created_at": t.created_at,
            }
        )

    all_txns.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    total = len(all_txns)
    page = all_txns[offset : offset + limit]

    return {"total": total, "count": len(page), "transactions": page}


@router.post("/wallet/connect")
async def connect_wallet(
    req: ConnectWalletRequest,
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Connect a wallet to chat for payments."""
    user_id = user.get("id")
    if not validate_address(req.address, req.chain):
        raise HTTPException(status_code=400, detail=f"Invalid wallet address for chain: {req.chain}")

    store = _store()
    nonce = generate_nonce(user_id)

    # Store connection
    connection = {
        "address": req.address,
        "chain": req.chain,
        "wallet_type": req.wallet_type,
        "connected_at": datetime.now(UTC).isoformat(),
        "nonce": nonce,
    }
    store.save_connection(user_id, connection)

    return {
        "status": "connected",
        "wallet": {
            "address": req.address,
            "chain": req.chain,
            "wallet_type": req.wallet_type,
        },
        "nonce": nonce,
        "message": "Wallet connected successfully. You can now send crypto in chat.",
    }


@router.post("/wallet/disconnect")
async def disconnect_wallet(
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Disconnect wallet from chat."""
    user_id = user.get("id")
    store = _store()
    store.remove_connection(user_id)
    return {"status": "disconnected", "message": "Wallet disconnected"}


@router.get("/wallet")
async def wallet_info(
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Get connected wallet info for the current user."""
    user_id = user.get("id")
    store = _store()
    connection = store.get_connection(user_id)

    if not connection:
        return {"connected": False, "wallet": None}

    return {
        "connected": True,
        "wallet": {
            "address": connection["address"],
            "chain": connection["chain"],
            "wallet_type": connection.get("wallet_type", "evm"),
            "connected_at": connection["connected_at"],
        },
    }


@router.get("/balance")
async def wallet_balance(
    chain: str = Query(default="ethereum"),
    token: str | None = Query(default=None),
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Get wallet balance for connected wallet."""
    user_id = user.get("id")
    store = _store()
    connection = store.get_connection(user_id)

    if not connection:
        raise HTTPException(status_code=400, detail="No wallet connected. Connect a wallet first.")

    address = connection["address"]
    chain_to_use = chain if chain else connection["chain"]

    balance = await get_wallet_balance(address, chain_to_use, token)

    return {
        "wallet": address,
        "chain": chain_to_use,
        "token": balance["token"],
        "balance": balance["balance_formatted"],
        "usd_value": balance.get("usd_value", "0"),
        "raw": balance.get("balance_raw", "0"),
    }


@router.get("/balances")
async def wallet_balances(
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Get all token balances for connected wallet across chains."""
    user_id = user.get("id")
    store = _store()
    connection = store.get_connection(user_id)

    if not connection:
        raise HTTPException(status_code=400, detail="No wallet connected")

    address = connection["address"]
    results: list[dict[str, Any]] = []

    # Check balances on major chains
    chains_to_check = ["ethereum", "solana", "base", "polygon"]
    for chain in chains_to_check:
        try:
            # Native balance
            bal = await get_wallet_balance(address, chain)
            if Decimal(bal.get("balance_formatted", "0")) > 0:
                results.append(bal)

            # USDC balance
            usdc_bal = await get_wallet_balance(address, chain, "USDC")
            if Decimal(usdc_bal.get("balance_formatted", "0")) > 0:
                results.append(usdc_bal)
        except Exception as e:
            logger.warning(f"Balance check failed for {chain}: {e}")

    return {
        "address": address,
        "balances": results,
        "count": len(results),
    }


@router.post("/swap/quote")
async def swap_quote(
    req: SwapQuoteRequest,
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Get a swap quote (simulated — real quotes require 0x or Jupiter API)."""
    try:
        amount = parse_amount(req.amount)
        if amount <= 0:
            raise HTTPException(status_code=400, detail="Amount must be positive")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    from_price = await get_token_price(req.from_token)
    to_price = await get_token_price(req.to_token)

    if from_price == 0 or to_price == 0:
        raise HTTPException(status_code=400, detail="Could not fetch price for one or both tokens")

    usd_value = amount * from_price
    estimated_output = usd_value / to_price

    return {
        "from_token": req.from_token.upper(),
        "to_token": req.to_token.upper(),
        "from_amount": req.amount,
        "estimated_output": str(estimated_output),
        "usd_value": str(usd_value),
        "chain": req.chain,
        "price_impact": "0.05%",  # Simulated
        "route": f"{req.from_token.upper()} → {req.to_token.upper()}",
        "note": "Use 0x API or Jupiter for live quotes",
    }


@router.post("/gas-estimate")
async def gas_estimate(
    from_addr: str = Query(..., description="Sender address"),
    to_addr: str = Query(..., description="Recipient address"),
    amount: str = Query(..., description="Amount in wei"),
    chain: str = Query(default="ethereum"),
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Estimate gas for a transfer."""
    gas = await estimate_gas(chain, from_addr, to_addr, amount)
    return gas


# ── Token-Gated Rooms ────────────────────────────────────────────


@router.post("/rooms/{room_id}/token-gate")
async def set_token_gate(
    room_id: str,
    req: TokenGateRequest,
    user: dict = Depends(require_auth),
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
    _store().save_token_gate(gate)

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
    user: dict | None = Depends(get_current_user),
) -> dict[str, Any]:
    """Get token gate requirements for a room."""
    gate = _store().get_token_gate(room_id)
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
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Remove token gate from a room."""
    _store().remove_gate(room_id)
    return {"room_id": room_id, "gated": False, "status": "removed"}


@router.post("/rooms/{room_id}/verify-gate")
async def verify_room_gate(
    room_id: str,
    req: VerifyGateRequest,
    user: dict = Depends(require_auth),
) -> dict[str, Any]:
    """Verify a wallet meets token gate requirements."""
    gate = _store().get_token_gate(room_id) or _store().get_nft_gate(room_id)
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
    user: dict = Depends(require_auth),
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

    _store().save_nft_gate(gate)

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
    user: dict | None = Depends(get_current_user),
) -> dict[str, Any]:
    """Get NFT gate requirements for a room."""
    gate = _store().get_nft_gate(room_id)
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


@router.post("/parse-command")
async def parse_chat_command(
    content: str = Query(..., description="Chat message content"),
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Parse a chat message for Web3 commands (/pay, /tip, /split, /request).

    The frontend can call this to check if a message contains a Web3 command
    before sending it as a regular message.
    """
    parsed = verify_payment_command(content)
    if parsed is None:
        return {"is_command": False, "parsed": None}
    return {"is_command": True, "parsed": parsed}
