"""
Web3 Chat Router - Wallet Routes
===================================
Wallet connect/disconnect, balance queries, swap quotes, gas estimation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import require_auth
from app.core.logging import get_logger
from app.routers.web3_chat.models import (  # noqa: TC001 — Pydantic request models, needs runtime resolution
    ConnectWalletRequest,
    SwapQuoteRequest,
)
from app.services.web3_chat import (
    estimate_gas,
    generate_nonce,
    get_store,
    get_token_price,
    get_wallet_balance,
    parse_amount,
    validate_address,
)

logger = get_logger("web3_chat_router")

router = APIRouter()


@router.post("/wallet/connect")
async def connect_wallet(
    req: ConnectWalletRequest,
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Connect a wallet to chat for payments."""
    user_id = cast("str", user.get("id"))
    if not validate_address(req.address, req.chain):
        raise HTTPException(status_code=400, detail=f"Invalid wallet address for chain: {req.chain}")

    store = get_store()
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
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Disconnect wallet from chat."""
    user_id = cast("str", user.get("id"))
    store = get_store()
    store.remove_connection(user_id)
    return {"status": "disconnected", "message": "Wallet disconnected"}


@router.get("/wallet")
async def wallet_info(
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get connected wallet info for the current user."""
    user_id = cast("str", user.get("id"))
    store = get_store()
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
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get wallet balance for connected wallet."""
    user_id = cast("str", user.get("id"))
    store = get_store()
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
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Get all token balances for connected wallet across chains."""
    user_id = cast("str", user.get("id"))
    store = get_store()
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
    user: dict[str, Any] = Depends(require_auth),
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
        "route": f"{req.from_token.upper()} \u2192 {req.to_token.upper()}",
        "note": "Use 0x API or Jupiter for live quotes",
    }


@router.post("/gas-estimate")
async def gas_estimate(
    from_addr: str = Query(..., description="Sender address"),
    to_addr: str = Query(..., description="Recipient address"),
    amount: str = Query(..., description="Amount in wei"),
    chain: str = Query(default="ethereum"),
    user: dict[str, Any] = Depends(require_auth),
) -> dict[str, Any]:
    """Estimate gas for a transfer."""
    gas = await estimate_gas(chain, from_addr, to_addr, amount)
    return gas
