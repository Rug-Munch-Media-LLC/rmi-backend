"""
Binance Web3 API adapter for Wallet PnL Analyzer.
All endpoints are free and require no authentication.
"""

import httpx

BASE_URL = "https://web3.binance.com"

CHAIN_IDS = {
    "bsc": "56",
    "eth": "1",
    "base": "8453",
    "arb": "42161",
    "polygon": "137",
}

CHAIN_NAMES = {
    "56": "BSC",
    "1": "ETH",
    "8453": "BASE",
    "42161": "ARB",
    "137": "POLYGON",
}

# Headers required by the wallet holdings endpoint
_HEADERS = {
    "Accept-Encoding": "identity",
    "clienttype": "web",
    "clientversion": "1.2.0",
}


def _get(url, params=None, timeout=10):
    resp = httpx.get(url, params=params, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "000000":
        raise ConnectionError(f"API error: {data.get('message', 'unknown')}")
    return data.get("data", {})


def get_wallet_holdings(address: str, chain_id: str) -> list:
    """
    Fetch all token holdings for a wallet address on a specific chain.

    Args:
        address: Wallet address (e.g. "0xAb58...")
        chain_id: Chain ID string (e.g. "56", "1")

    Returns:
        List of token dicts with: symbol, name, price, remainQty,
        percentChange24h, contractAddress, riskLevel
    """
    url = f"{BASE_URL}/bapi/defi/v3/public/wallet-direct/buw/wallet/address/pnl/active-position-list"
    all_tokens = []
    offset = 0

    max_pages = 5  # cap at 100 tokens to avoid long-running loops

    for _ in range(max_pages):
        params = {"address": address.lower(), "chainId": chain_id, "offset": offset}
        data = _get(url, params=params)
        batch = data.get("list") or []
        all_tokens.extend(batch)

        if len(batch) < 20:
            break
        offset += len(batch)

    return all_tokens
