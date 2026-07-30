"""Helius RPC credential pool and wallet data fetching."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

_HELIUS_KEYS = None
_HELIUS_INDEX = 0


def _get_helius_key() -> str | None:
    """Round-robin through Helius API keys. Returns None if none configured."""
    global _HELIUS_KEYS, _HELIUS_INDEX
    if _HELIUS_KEYS is None:
        import os

        keys = []
        k1 = os.getenv("HELIUS_API_KEY", "").strip()
        if k1:
            keys.append(k1)
        k2 = os.getenv("HELIUS_API_KEY_2", "").strip()
        if k2:
            keys.append(k2)
        k3 = os.getenv("HELIUS_API_KEY_3", "").strip()
        if k3:
            keys.append(k3)
        _HELIUS_KEYS = keys
    if not _HELIUS_KEYS:
        return None
    key = _HELIUS_KEYS[_HELIUS_INDEX % len(_HELIUS_KEYS)]
    _HELIUS_INDEX += 1
    return key


async def _helius_call(method: str, params: list) -> dict | None:
    """Call Helius RPC with credential pool fallback."""
    for _attempt in range(3):
        key = _get_helius_key()
        if not key:
            return None
        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(
                    f"https://mainnet.helius-rpc.com/?api-key={key}",
                    json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" not in data:
                        return data
                if resp.status_code in (401, 429):
                    continue
        except (ConnectionError, TimeoutError, httpx.HTTPError):
            logger.debug("helius call failed, trying next key", exc_info=True)
            continue
    return None


async def _fetch_helius_wallet(address: str) -> dict[str, Any]:
    """Fetch wallet age, tx count, balances, and token holdings from Helius."""
    result = {}

    bal_data = await _helius_call(
        "getTokenAccountsByOwner",
        [
            address,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    )
    if bal_data:
        accounts = (bal_data.get("result", {}) or {}).get("value", [])
        tokens = []
        for acc in accounts:
            info = acc.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
            amt = float(info.get("tokenAmount", {}).get("uiAmount", 0) or 0)
            mint = info.get("mint", "")
            if mint and amt > 0:
                tokens.append({"mint": mint, "amount": amt})
        result["tokens"] = tokens
        result["token_count"] = len(tokens)
        result["data_sources"] = ["helius"]

    sig_data = await _helius_call("getSignaturesForAddress", [address, {"limit": 100}])
    if sig_data:
        sigs = sig_data.get("result", []) or []
        if sigs:
            result["tx_count_sample"] = len(sigs)
            result["last_tx"] = sigs[0].get("blockTime")
            first = sigs[-1].get("blockTime")
            if first:
                age_seconds = datetime.now(UTC).timestamp() - first
                result["wallet_age_days"] = int(age_seconds / 86400)
                result["tx_count"] = len(sigs)
                result["data_sources"] = [*result.get("data_sources", []), "helius_tx"]

    sol_data = await _helius_call("getBalance", [address])
    if sol_data:
        bal = sol_data.get("result", {}).get("value", 0)
        result["sol_balance"] = bal / 1e9 if bal else 0

    return result


async def _fetch_moralis_wallet(address: str, chain: str) -> dict[str, Any]:
    """Fetch EVM wallet data from Moralis."""
    import os

    key = os.getenv("MORALIS_API_KEY", "").strip()
    if not key:
        return {}

    chain_map = {
        "ethereum": "eth",
        "bsc": "bsc",
        "polygon": "polygon",
        "arbitrum": "arbitrum",
        "base": "base",
        "optimism": "optimism",
        "avalanche": "avalanche",
        "fantom": "fantom",
    }
    moralis_chain = chain_map.get(chain, "eth")

    result = {}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            headers = {"X-API-Key": key}

            resp = await client.get(
                f"https://deep-index.moralis.io/api/v2.2/{address}/erc20",
                params={"chain": moralis_chain},
                headers=headers,
            )
            if resp.status_code == 200:
                tokens_data = resp.json()
                tokens = []
                for t in tokens_data[:50]:
                    amt = float(t.get("balance_formatted", 0) or 0)
                    if amt > 0:
                        tokens.append(
                            {
                                "mint": t.get("token_address", ""),
                                "amount": amt,
                                "symbol": t.get("symbol", ""),
                            }
                        )
                result["tokens"] = tokens
                result["token_count"] = len(tokens)
                result["data_sources"] = [*result.get("data_sources", []), "moralis"]

            resp2 = await client.get(
                f"https://deep-index.moralis.io/api/v2.2/{address}/balance",
                params={"chain": moralis_chain},
                headers=headers,
            )
            if resp2.status_code == 200:
                bal = resp2.json().get("balance", 0)
                if bal:
                    result["native_balance"] = float(bal) / 1e18

            resp3 = await client.get(
                f"https://deep-index.moralis.io/api/v2.2/{address}",
                params={"chain": moralis_chain},
                headers=headers,
            )
            if resp3.status_code == 200:
                wallet_data = resp3.json()
                if wallet_data.get("transactions"):
                    txs = wallet_data["transactions"]
                    result["tx_count"] = len(txs)
                    result["data_sources"] = [*result.get("data_sources", []), "moralis_tx"]
    except (ConnectionError, OSError, TimeoutError):
        logger.debug("moralis wallet fetch failed", exc_info=True)

    return result


ETHERSCAN_APIS = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
    "polygon": "https://api.polygonscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "base": "https://api.basescan.org/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
    "avalanche": "https://api.snowtrace.io/api",
    "fantom": "https://api.ftmscan.com/api",
}


async def _fetch_etherscan_wallet(address: str, chain: str) -> dict[str, Any]:
    """Fetch EVM wallet tx count and age from Etherscan family."""
    if chain not in ETHERSCAN_APIS:
        return {}

    result = {}
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            api_url = ETHERSCAN_APIS[chain]
            resp = await client.get(
                api_url,
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": 10,
                    "sort": "asc",
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "1":
                    txs = data.get("result", [])
                    if txs:
                        first_ts = int(txs[0].get("timeStamp", 0))
                        last_ts = int(txs[-1].get("timeStamp", 0))
                        if first_ts:
                            result["wallet_age_days"] = int((datetime.now(UTC).timestamp() - first_ts) / 86400)
                        if last_ts:
                            result["last_tx"] = last_ts
                        result["tx_count_sample"] = len(txs)
                        result["data_sources"] = ["etherscan"]
    except (ConnectionError, OSError, TimeoutError):
        logger.debug("etherscan wallet fetch failed", exc_info=True)

    return result
