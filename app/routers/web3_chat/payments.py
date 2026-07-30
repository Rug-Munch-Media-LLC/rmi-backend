"""
Web3 Chat Router - Payment Routes
===================================
Pay, confirm, tip, split, and request payment endpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import require_auth
from app.core.logging import get_logger
from app.routers.web3_chat.models import PayRequest, RequestPayment, SplitRequest, TipRequest
from app.services.web3_chat import (
    PaymentRequest,
    PaymentStatus,
    SplitPayment,
    TipRecord,
    format_tx_link,
    get_store,
    get_token_price,
    parse_amount,
    simulate_payment,
    validate_address,
)

logger = get_logger("web3_chat_router")

router = APIRouter()


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
    get_store().save_payment(payment)

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
    store = get_store()
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
    get_store().save_tip(tip)

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
    get_store().save_split(split)

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
