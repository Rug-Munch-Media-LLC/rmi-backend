"""
SENTINEL - Fund Flow Fetchers
==============================
API fetchers for EVM and Solana wallet data.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("fund_flow_visualizer.fetchers")


async def _fetch_evm_wallets(token_address: str, chain: str) -> dict[str, Any]:
    """Fetch key wallets for an EVM token using block explorer APIs."""
    result: dict[str, Any] = {
        "creator": None,
        "funders": [],
        "sellers": [],
        "holders": [],
    }

    # Determine explorer API
    if chain == "base":
        base_url = "https://api.basescan.org/api"
        key_env = "BASESCAN_KEY"
    elif chain == "bsc":
        base_url = "https://api.bscscan.com/api"
        key_env = "BSCSCAN_KEY"
    else:
        base_url = "https://api.etherscan.io/api"
        key_env = "ETHERSCAN_KEY"

    api_key = os.getenv(key_env, "")
    params: dict[str, Any]

    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1) Get contract creator from explorer
        try:
            params = {
                "module": "contract",
                "action": "getcontractcreation",
                "contractaddresses": token_address,
            }
            if api_key:
                params["apikey"] = api_key
            resp = await client.get(base_url, params=params)
            data = resp.json()
            if data.get("status") == "1" and data.get("result"):
                creator = data["result"][0].get("contractCreator", "")
                if creator:
                    result["creator"] = creator
        except Exception as e:
            logger.debug("EVM creator fetch failed: %s", e)

        # 2) Get first token transfers (earliest buyers/funders)
        try:
            params = {
                "module": "account",
                "action": "tokentx",
                "contractaddress": token_address,
                "page": 1,
                "offset": 50,
                "sort": "asc",
            }
            if api_key:
                params["apikey"] = api_key
            resp = await client.get(base_url, params=params)
            data = resp.json()
            if data.get("status") == "1" and data.get("result"):
                transfers = data["result"][:30]
                seen = set()
                for tx in transfers:
                    from_addr = tx.get("from", "")
                    to_addr = tx.get("to", "")
                    value = int(tx.get("value", "0") or "0")
                    timestamp = tx.get("timeStamp", "")

                    # Funders = first receivers (early buyers)
                    if to_addr and to_addr.lower() != token_address.lower() and to_addr not in seen:
                        seen.add(to_addr)
                        result["funders"].append(
                            {
                                "address": to_addr,
                                "amount_raw": value,
                                "timestamp": timestamp,
                            }
                        )

                    # Sellers = senders who transferred out early
                    if from_addr and from_addr.lower() != token_address.lower() and from_addr not in seen:
                        seen.add(from_addr)
                        result["sellers"].append(
                            {
                                "address": from_addr,
                                "amount_raw": value,
                                "timestamp": timestamp,
                            }
                        )
        except Exception as e:
            logger.debug("EVM transfer fetch failed: %s", e)

        # 3) Get top token holders
        try:
            params = {
                "module": "token",
                "action": "tokenholderlist",
                "contractaddress": token_address,
                "page": 1,
                "offset": 10,
            }
            if api_key:
                params["apikey"] = api_key
            resp = await client.get(base_url, params=params)
            data = resp.json()
            if data.get("status") == "1" and data.get("result"):
                for holder in data["result"][:10]:
                    addr = holder.get("TokenHolderAddress", "")
                    balance = int(holder.get("TokenHolderQuantity", "0") or "0")
                    if addr:
                        result["holders"].append(
                            {
                                "address": addr,
                                "balance_raw": balance,
                            }
                        )
        except Exception as e:
            logger.debug("EVM holder fetch failed: %s", e)

    return result


async def _fetch_solana_wallets(token_address: str) -> dict[str, Any]:
    """Fetch key wallets for a Solana token using Helius/Solscan."""
    result: dict[str, Any] = {
        "creator": None,
        "funders": [],
        "sellers": [],
        "holders": [],
    }

    helius_key = os.getenv("HELIUS_API_KEY", "")

    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1) Get token creation info from Helius DAS API
        if helius_key:
            try:
                resp = await client.post(
                    f"https://mainnet.helius-rpc.com/?api-key={helius_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": "1",
                        "method": "getAsset",
                        "params": {"id": token_address},
                    },
                )
                data = resp.json()
                if isinstance(data, dict):
                    info = data.get("result")
                    if info and isinstance(info, dict):
                        # DAS API returns authorities array
                        authorities = info.get("authorities", [])
                        if authorities and isinstance(authorities, list) and len(authorities) > 0:
                            creator = authorities[0].get("address", "")
                            if creator:
                                result["creator"] = creator
            except Exception as e:
                logger.debug("Helius token metadata fetch failed: %s", e)

            # 2) Get transaction history to find early buyers/sellers
            try:
                url = f"https://mainnet.helius-rpc.com/v0/addresses/{token_address}/transactions?api-key={helius_key}"
                resp = await client.get(url)
                txs = resp.json()
                if isinstance(txs, list):
                    seen = set()
                    for tx in txs[:30]:
                        # NativeTransfers contain SOL movements
                        native = tx.get("nativeTransfers", [])
                        for transfer in native:
                            from_addr = transfer.get("fromUserAccount", "")
                            to_addr = transfer.get("toUserAccount", "")
                            amount = transfer.get("amount", 0) / 1e9  # lamports to SOL
                            if to_addr and to_addr not in seen:
                                seen.add(to_addr)
                                result["funders"].append(
                                    {
                                        "address": to_addr,
                                        "amount_sol": amount,
                                    }
                                )
                            if from_addr and from_addr not in seen:
                                seen.add(from_addr)
                                result["sellers"].append(
                                    {
                                        "address": from_addr,
                                        "amount_sol": amount,
                                    }
                                )
                        # Also check token transfers
                        token_transfers = tx.get("tokenTransfers", [])
                        for tt in token_transfers:
                            from_addr = tt.get("fromUserAccount", "")
                            to_addr = tt.get("toUserAccount", "")
                            amount = tt.get("tokenAmount", 0)
                            if to_addr and to_addr != token_address and to_addr not in seen:
                                seen.add(to_addr)
                                result["funders"].append(
                                    {
                                        "address": to_addr,
                                        "amount_raw": amount,
                                    }
                                )
                            if from_addr and from_addr != token_address and from_addr not in seen:
                                seen.add(from_addr)
                                result["sellers"].append(
                                    {
                                        "address": from_addr,
                                        "amount_raw": amount,
                                    }
                                )
            except Exception as e:
                logger.debug("Helius transaction fetch failed: %s", e)

        # 3) Get top holders from Solscan
        try:
            url = f"https://api.solscan.io/v2/token/holders?address={token_address}&offset=0&size=10"
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = await client.get(url, headers=headers)
            data = resp.json()
            if isinstance(data, dict) and data.get("data"):
                for holder in data["data"][:10]:
                    addr = holder.get("owner", holder.get("address", ""))
                    balance = holder.get("amount", holder.get("balance", 0))
                    if addr:
                        result["holders"].append(
                            {
                                "address": addr,
                                "balance_raw": balance,
                            }
                        )
            elif isinstance(data, list):
                for holder in data[:10]:
                    addr = holder.get("owner", holder.get("address", ""))
                    if addr:
                        result["holders"].append(
                            {
                                "address": addr,
                                "balance_raw": holder.get("amount", 0),
                            }
                        )
        except Exception as e:
            logger.debug("Solscan holders fetch failed: %s", e)

    return result
