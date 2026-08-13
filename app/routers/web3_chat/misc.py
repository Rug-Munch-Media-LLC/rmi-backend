"""
Web3 Chat Router - Misc Routes
=================================
Transaction history, command parsing, and other miscellaneous endpoints.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Depends, Query

from app.auth import get_current_user, require_auth
from app.core.logging import get_logger
from app.services.web3_chat import (
    format_tx_link,
    get_store,
    verify_payment_command,
)

logger = get_logger("web3_chat_router")

router = APIRouter()


@router.get("/transactions")
async def transaction_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get user's transaction history (payments, tips, splits)."""
    user_id = cast("str", user.get("id"))
    store = get_store()

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


@router.post("/parse-command")
async def parse_chat_command(
    content: str = Query(..., description="Chat message content"),
    user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Parse a chat message for Web3 commands (/pay, /tip, /split, /request).

    The frontend can call this to check if a message contains a Web3 command
    before sending it as a regular message.
    """
    parsed = verify_payment_command(content)
    if parsed is None:
        return {"is_command": False, "parsed": None}
    return {"is_command": True, "parsed": parsed}
