"""
Web3 Chat Verification
======================
Token/NFT/min-value holdings verification and payment simulation.
"""

from __future__ import annotations

from contextlib import suppress
from decimal import Decimal
from typing import Any

from app.core.logging import get_logger
from app.services.web3_chat.models import SUPPORTED_CHAINS, SUPPORTED_TOKENS, TOKEN_ADDRESSES
from app.services.web3_chat.rpc import (
    _evm_rpc_call,
    _get_client,
    _sol_get_balance,
    _sol_token_balance,
    estimate_gas,
    get_token_price,
)
from app.services.web3_chat.utils import validate_address

logger = get_logger("web3_chat")


# ── Token Verification ──


async def verify_token_holdings(address: str, token_address: str, chain: str, min_amount: str) -> dict[str, Any]:
    """Verify a wallet holds at least min_amount of a specific token."""
    result: dict[str, Any] = {
        "verified": False,
        "balance": "0",
        "required": min_amount,
        "token_address": token_address,
        "chain": chain,
        "error": None,
    }
    try:
        if chain == "solana":
            balance = await _sol_token_balance(address, token_address)
            result["balance"] = str(balance)
            result["verified"] = Decimal(str(balance)) >= Decimal(min_amount)
        else:
            # EVM token verification
            data = f"0x70a08231{'0' * 24}{address[2:].lower()}"
            balance_hex = await _evm_rpc_call(
                chain,
                "eth_call",
                [
                    {
                        "to": token_address,
                        "data": data,
                    },
                    "latest",
                ],
            )
            if balance_hex and balance_hex != "0x":
                bal = Decimal(str(int(balance_hex, 16)))
                # Try to determine decimals (default 18)
                decimals_hex = await _evm_rpc_call(
                    chain,
                    "eth_call",
                    [
                        {
                            "to": token_address,
                            "data": "0x313ce567",
                        },
                        "latest",
                    ],
                )
                decimals = 18
                if decimals_hex and decimals_hex != "0x":
                    with suppress(ValueError, TypeError):
                        decimals = int(decimals_hex, 16)
                human_balance = bal / Decimal(str(10**decimals))
                result["balance"] = str(human_balance)
                result["verified"] = human_balance >= Decimal(min_amount)
    except Exception as e:
        logger.error(f"Token verification error: {e}")
        result["error"] = str(e)
    return result


# ── NFT Verification ──


async def verify_nft_holding(
    address: str,
    chain: str,
    contract: str | None = None,
    collection_name: str | None = None,
    creator: str | None = None,
) -> dict[str, Any]:
    """Verify a wallet holds a specific NFT or any from a collection."""
    result: dict[str, Any] = {
        "verified": False,
        "nfts_found": 0,
        "nft_names": [],
        "chain": chain,
        "error": None,
    }
    try:
        if chain == "solana":
            return await _verify_sol_nft(address, creator=creator, collection=collection_name)
        else:
            return await _verify_evm_nft(address, chain, contract)
    except Exception as e:
        logger.error(f"NFT verification error: {e}")
        result["error"] = str(e)
    return result


async def _verify_evm_nft(address: str, chain: str, contract: str | None) -> dict[str, Any]:
    """Verify EVM NFT holding via ERC-721/ERC-1155."""
    result: dict[str, Any] = {
        "verified": False,
        "nfts_found": 0,
        "nft_names": [],
        "chain": chain,
        "error": None,
    }
    if not contract or contract == "any":
        # We can't enumerate all NFTs without an indexer
        result["error"] = "Specific contract address required for EVM NFT verification"
        return result
    try:
        # Check balanceOf for ERC-721 (0x70a08231)
        data = f"0x70a08231{'0' * 24}{address[2:].lower()}"
        balance = await _evm_rpc_call(
            chain,
            "eth_call",
            [
                {
                    "to": contract,
                    "data": data,
                },
                "latest",
            ],
        )
        if balance and balance != "0x":
            count = int(balance, 16)
            result["nfts_found"] = count
            result["verified"] = count > 0
        else:
            # Try ERC-1155 balanceOfBatch — skip for now, assume no
            result["nfts_found"] = 0
            result["verified"] = False
    except Exception as e:
        result["error"] = str(e)
    return result


async def _verify_sol_nft(address: str, creator: str | None = None, collection: str | None = None) -> dict[str, Any]:
    """Verify Solana Metaplex NFT holding."""
    result: dict[str, Any] = {
        "verified": False,
        "nfts_found": 0,
        "nft_names": [],
        "chain": "solana",
        "error": None,
    }
    try:
        client = await _get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getAssetsByOwner",
            "params": [address, {"page": 1, "limit": 50}],
        }
        resp = await client.post(
            "https://api.mainnet-beta.solana.com",  # Would use Helius/helius for asset API
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("result", {}).get("items", [])
            for item in items:
                if collection:
                    # Check collection match
                    coll = item.get("collection", {})
                    if coll.get("name", "").lower() == collection.lower():
                        result["nfts_found"] += 1
                        result["nft_names"].append(item.get("content", {}).get("metadata", {}).get("name", "Unknown"))
                elif creator:
                    creators = item.get("creators", [])
                    if any(c.get("address") == creator for c in creators):
                        result["nfts_found"] += 1
                        result["nft_names"].append(item.get("content", {}).get("metadata", {}).get("name", "Unknown"))
                else:
                    result["nfts_found"] += 1
                    result["nft_names"].append(item.get("content", {}).get("metadata", {}).get("name", "Unknown"))
            result["verified"] = result["nfts_found"] > 0
    except Exception as e:
        logger.warning(f"Solana NFT verification error: {e}")
        result["error"] = str(e)
    return result


# ── Min Value Verification ──


async def verify_min_value_holdings(address: str, chain: str, min_usd: str) -> dict[str, Any]:
    """Verify wallet holds at least min_usd worth of any token."""
    result: dict[str, Any] = {
        "verified": False,
        "total_value_usd": "0",
        "required": min_usd,
        "error": None,
    }
    try:
        if chain == "solana":
            # Get SOL balance
            sol_balance = await _sol_get_balance(address)
            sol_price = await get_token_price("SOL")
            sol_value = (Decimal(str(sol_balance)) / Decimal("1000000000")) * sol_price

            # Get USDC on Solana
            usdc_balance = await _sol_token_balance(address, TOKEN_ADDRESSES["solana"]["USDC"])
            usdc_price = await get_token_price("USDC")
            usdc_value = usdc_balance * usdc_price

            total = sol_value + usdc_value
            result["total_value_usd"] = str(total)
            result["verified"] = total >= Decimal(min_usd)
        else:
            # EVM chain - get native balance
            bal_hex = await _evm_rpc_call(chain, "eth_getBalance", [address, "latest"])
            native_price = await get_token_price(SUPPORTED_CHAINS.get(chain, {}).get("native", "ETH"))
            total = Decimal("0")
            if bal_hex:
                native_bal = Decimal(str(int(bal_hex, 16))) / Decimal("1000000000000000000")
                total = native_bal * native_price

            # Also check USDC on this chain
            chain_usdc = TOKEN_ADDRESSES.get(chain, {}).get("USDC")
            if chain_usdc:
                usdc_data = f"0x70a08231{'0' * 24}{address[2:].lower()}"
                usdc_hex = await _evm_rpc_call(
                    chain,
                    "eth_call",
                    [
                        {
                            "to": chain_usdc,
                            "data": usdc_data,
                        },
                        "latest",
                    ],
                )
                if usdc_hex and usdc_hex != "0x":
                    usdc_bal = Decimal(str(int(usdc_hex, 16))) / Decimal("1000000")
                    total += usdc_bal  # USDC ~$1

            result["total_value_usd"] = str(total)
            result["verified"] = total >= Decimal(min_usd)
    except Exception as e:
        result["error"] = str(e)
    return result


# ── Payment Simulation ──


async def simulate_payment(from_addr: str, to_addr: str, amount: str, token: str, chain: str) -> dict[str, Any]:
    """Simulate a payment to estimate costs and validate feasibility."""
    # Validate addresses
    if not validate_address(from_addr, chain):
        return {"valid": False, "error": f"Invalid sender address for {chain}"}
    if chain != "lightning" and not validate_address(to_addr, chain):
        return {"valid": False, "error": f"Invalid recipient address for {chain}"}

    # Parse amount
    decimals = SUPPORTED_TOKENS.get(token.upper(), {}).get("decimals", 18)
    try:
        amount_parsed = Decimal(str(amount))
        if amount_parsed <= 0:
            return {"valid": False, "error": "Amount must be positive"}
    except (ValueError, ArithmeticError):
        return {"valid": False, "error": "Invalid amount"}

    # Get price
    price = await get_token_price(token)
    usd_value = amount_parsed * price

    # Estimate gas (EVM only)
    gas_est = None
    if chain in ("ethereum", "base", "polygon", "arbitrum", "optimism"):
        wei_amount = str(int(amount_parsed * Decimal(str(10**decimals))))
        gas_est = await estimate_gas(chain, from_addr, to_addr, wei_amount)

    return {
        "valid": True,
        "from": from_addr,
        "to": to_addr,
        "amount": amount,
        "token": token.upper(),
        "chain": chain,
        "usd_value": str(usd_value),
        "gas_estimation": gas_est,
        "price_usd": str(price),
    }
