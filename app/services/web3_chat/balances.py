"""
Web3 Chat Balances
==================
Wallet balance fetching across multiple chains.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.services.web3_chat.models import SUPPORTED_CHAINS, SUPPORTED_TOKENS
from app.services.web3_chat.rpc import (
    _base_get_balance,
    _erc20_balance,
    _eth_get_balance,
    _evm_rpc_call,
    _sol_get_balance,
    get_token_price,
)

logger = get_logger("web3_chat")


async def get_wallet_balance(address: str, chain: str, token: str | None = None) -> dict[str, Any]:
    """Fetch wallet balance for native or token on a chain.

    Returns dict with balance, usd_value, and formatted string.
    """
    price = await get_token_price(token or chain.upper())
    result: dict[str, Any] = {
        "address": address,
        "chain": chain,
        "token": token or SUPPORTED_CHAINS.get(chain, {}).get("native", "ETH"),
        "balance_raw": "0",
        "balance_formatted": "0",
        "usd_value": "0",
        "error": None,
    }

    try:
        if chain == "ethereum":
            token_name = (token or "ETH").upper()
            native = token is None or token_name == "ETH"
            if native:
                # Use eth_getBalance via public RPC
                balance = await _eth_get_balance(address)
            else:
                balance = await _erc20_balance(address, chain, token_name)
            result["balance_raw"] = str(balance)
            token_decimals = int(SUPPORTED_TOKENS.get(token_name, {}).get("decimals", 18) or 18) if not native else 18
            form = balance / (10**token_decimals)
            result["balance_formatted"] = str(form)
            result["usd_value"] = str(form * price)

        elif chain == "solana":
            sol_balance = await _sol_get_balance(address)
            result["balance_raw"] = str(sol_balance)
            form = Decimal(str(sol_balance)) / Decimal("1000000000")
            result["balance_formatted"] = str(form)
            result["usd_value"] = str(form * price)

        elif chain == "base":
            token_name = (token or "ETH").upper()
            native = token is None or token_name == "ETH"
            if native:
                balance = await _base_get_balance(address)
            else:
                balance = await _erc20_balance(address, chain, token_name)
            result["balance_raw"] = str(balance)
            form = balance / Decimal("1000000000000000000")
            result["balance_formatted"] = str(form)
            result["usd_value"] = str(form * price)

        elif chain == "lightning":
            result["balance_formatted"] = "Lightning balance via connected node"
            result["balance_raw"] = "0"

        else:
            # Polygon, Arbitrum, Optimism — use native RPC approach
            balance = await _evm_rpc_call(chain, "eth_getBalance", [address, "latest"])
            if balance is not None:
                bal_int = int(balance, 16)
                result["balance_raw"] = str(bal_int)
                form = Decimal(str(bal_int)) / Decimal("1000000000000000000")
                result["balance_formatted"] = str(form)
                result["usd_value"] = str(form * price)

    except Exception as e:
        logger.error(f"Balance fetch error for {address} on {chain}: {e}")
        result["error"] = str(e)

    return result
