"""
Web3 Chat RPC
=============
HTTP client, EVM/Solana RPC calls, token price, and gas estimation.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx

from app.core.logging import get_logger
from app.services.web3_chat.models import TOKEN_ADDRESSES

logger = get_logger("web3_chat")


# ── HTTP Clients ──

_http_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_keepalive_connections=10),
            headers={"User-Agent": "RugMunchRMI/1.0"},
        )
    return _http_client


# ── EVM RPC ──


async def _evm_rpc_call(chain: str, method: str, params: list[Any]) -> Any:
    """Make an EVM JSON-RPC call to the appropriate endpoint."""
    rpc_urls = {
        "ethereum": "https://ethereum-rpc.publicnode.com",
        "base": "https://base-rpc.publicnode.com",
        "polygon": "https://polygon-rpc.com",
        "arbitrum": "https://arbitrum-rpc.publicnode.com",
        "optimism": "https://optimism-rpc.publicnode.com",
    }
    url = rpc_urls.get(chain)
    if not url:
        return None
    try:
        client = await _get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
        resp = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if resp.status_code == 200:
            data = resp.json()
            return data.get("result")
    except Exception as e:
        logger.warning(f"EVM RPC error on {chain}: {e}")
    return None


async def _eth_get_balance(address: str) -> Decimal:
    """Get ETH balance via public RPC."""
    result = await _evm_rpc_call("ethereum", "eth_getBalance", [address, "latest"])
    if result:
        return Decimal(str(int(result, 16)))
    return Decimal("0")


async def _base_get_balance(address: str) -> Decimal:
    """Get Base ETH balance."""
    result = await _evm_rpc_call("base", "eth_getBalance", [address, "latest"])
    if result:
        return Decimal(str(int(result, 16)))
    return Decimal("0")


async def _sol_get_balance(address: str) -> int:
    """Get SOL balance via public RPC."""
    try:
        client = await _get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [address],
        }
        resp = await client.post(
            "https://api.mainnet-beta.solana.com",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("result", {}).get("value", 0)
    except Exception as e:
        logger.warning(f"Solana RPC error: {e}")
    return 0


async def _erc20_balance(address: str, chain: str, token: str) -> Decimal:
    """Get ERC-20 token balance."""
    token_addr = TOKEN_ADDRESSES.get(chain, {}).get(token)
    if not token_addr:
        return Decimal("0")
    # ERC-20 balanceOf ABI: 0x70a08231
    data = f"0x70a08231{'0' * 24}{address[2:].lower()}"
    result = await _evm_rpc_call(
        chain,
        "eth_call",
        [
            {
                "to": token_addr,
                "data": data,
            },
            "latest",
        ],
    )
    if result and result != "0x":
        return Decimal(str(int(result, 16)))
    return Decimal("0")


async def _sol_token_balance(address: str, token_mint: str) -> Decimal:
    """Get SPL token balance for a wallet."""
    try:
        client = await _get_client()
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                address,
                {"mint": token_mint},
                {"encoding": "jsonParsed"},
            ],
        }
        resp = await client.post(
            "https://api.mainnet-beta.solana.com",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code == 200:
            data = resp.json()
            accounts = data.get("result", {}).get("value", [])
            total = Decimal("0")
            for acc in accounts:
                info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                amount = info.get("tokenAmount", {}).get("uiAmount") or 0
                total += Decimal(str(amount))
            return total
    except Exception as e:
        logger.warning(f"Solana token balance error: {e}")
    return Decimal("0")


# ── Token Price ──


async def get_token_price(token: str) -> Decimal:
    """Get current USD price for a token via CoinGecko free API."""
    cg_id_map = {
        "ETH": "ethereum",
        "SOL": "solana",
        "USDC": "usd-coin",
        "USDT": "tether",
        "DAI": "dai",
        "BTC": "bitcoin",
    }
    cg_id = cg_id_map.get(token.upper(), token.lower())
    try:
        client = await _get_client()
        resp = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cg_id, "vs_currencies": "usd"},
        )
        if resp.status_code == 200:
            data = resp.json()
            price = data.get(cg_id, {}).get("usd", 0)
            return Decimal(str(price))
    except Exception as e:
        logger.warning(f"Price fetch failed for {token}: {e}")
    return Decimal("0")


# ── Gas Estimation ──


async def estimate_gas(chain: str, from_addr: str, to_addr: str, amount_wei: str) -> dict[str, Any]:
    """Estimate gas for a transfer on EVM chains."""
    result: dict[str, Any] = {
        "chain": chain,
        "estimated_gas": None,
        "estimated_cost_eth": None,
        "estimated_cost_usd": None,
        "error": None,
    }
    try:
        # Get gas price
        gas_price_hex = await _evm_rpc_call(chain, "eth_gasPrice", [])
        if not gas_price_hex:
            result["error"] = "Could not fetch gas price"
            return result
        gas_price = int(gas_price_hex, 16)

        # Estimate gas limit (standard transfer: 21000)
        # For token transfers, this would be higher
        gas_limit = 21000

        eth_price = await get_token_price("ETH")
        cost_wei = gas_price * gas_limit
        cost_eth = Decimal(str(cost_wei)) / Decimal("1000000000000000000")
        cost_usd = cost_eth * eth_price

        result["estimated_gas"] = gas_limit
        result["estimated_cost_eth"] = str(cost_eth)
        result["estimated_cost_usd"] = str(cost_usd)
        result["gas_price_gwei"] = str(Decimal(str(gas_price)) / Decimal("1000000000"))
    except Exception as e:
        logger.warning(f"Gas estimation error: {e}")
        result["error"] = str(e)
    return result
