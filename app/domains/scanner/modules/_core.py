"""Core scanner module — shared constants and helpers."""

import time
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Chain ID mappings ──────────────────────────────────────────────────

GOPLUS_CHAIN_IDS: dict[str, str] = {
    "solana": "solana",
    "ethereum": "1",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "polygon": "137",
    "avalanche": "43114",
    "optimism": "10",
    "fantom": "250",
    "linea": "59144",
    "zksync": "324",
}

EXPLORER_APIS: dict[str, str] = {
    "ethereum": "https://api.etherscan.io/api",
    "bsc": "https://api.bscscan.com/api",
    "polygon": "https://api.polygonscan.com/api",
    "arbitrum": "https://api.arbiscan.io/api",
    "optimism": "https://api-optimistic.etherscan.io/api",
    "base": "https://api.basescan.org/api",
    "avalanche": "https://api.snowtrace.io/api",
    "fantom": "https://api.ftmscan.com/api",
}

# Blockscout per-chain URLs
BLOCKSCOUT_URLS: dict[str, str] = {
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
    "arbitrum": "https://arbitrum.blockscout.com",
    "polygon": "https://polygon.blockscout.com",
    "optimism": "https://optimism.blockscout.com",
    "linea": "https://linea.blockscout.com",
    "zksync": "https://zksync.blockscout.com",
    "bsc": "https://bsc.blockscout.com",
    "avalanche": "https://avalanche.blockscout.com",
}

# DeFiLlama API base
DEFILLAMA_API = "https://api.llama.fi"

SOLANA_RPC_URLS = [
    "https://api.mainnet-beta.solana.com",
    "https://solana-api.syndica.io/access-token/free",
    "https://rpc.ankr.com/solana",
    "https://solana.publicnode.com",
]

SOLANA_RPC_TIMEOUT = 10.0


async def _solana_rpc_call(method: str, params: list[Any]) -> dict[str, Any] | None:
    """Call Solana RPC with fallback across multiple endpoints."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for rpc_url in SOLANA_RPC_URLS:
        try:
            async with httpx.AsyncClient(timeout=SOLANA_RPC_TIMEOUT) as client:
                resp = await client.post(rpc_url, json=body)
                if resp.status_code == 200:
                    data = resp.json()
                    if data and isinstance(data, dict) and "result" in data:
                        return data
                logger.debug("solana_rpc_error", url=rpc_url.split("/")[2], status=resp.status_code)
        except httpx.TimeoutException:
            logger.debug("solana_rpc_timeout", url=rpc_url.split("/")[2])
        except Exception as e:
            logger.debug("solana_rpc_failed", url=rpc_url.split("/")[2], error=str(e)[:50])
    return None


# ── Shared helpers ────────────────────────────────────────────────────


async def _fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    req_timeout: float = 15.0,
) -> dict[str, Any] | None:
    """Fetch JSON from an HTTP endpoint with error handling."""
    try:
        async with httpx.AsyncClient(timeout=req_timeout) as client:
            if json_body:
                resp = await client.post(url, json=json_body)
            else:
                resp = await client.get(url, params=params)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("http_error", url=url, status=resp.status_code)
    except Exception as e:
        logger.debug("http_failed", url=url, error=str(e))
    return None


async def _fetch_with_fallback(
    urls: list[str],
    req_timeout: float = 15.0,
    json_mode: bool = True,
) -> tuple[Any, str]:
    """Try URLs in order, return first successful response."""
    async with httpx.AsyncClient(timeout=req_timeout) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    if json_mode:
                        return resp.json(), url
                    return resp.text, url
            except Exception as e:
                logger.debug(f"failed {url}: {e}")
                continue
    return {}, ""


# ── 1. GoPlus Security ────────────────────────────────────────────────
