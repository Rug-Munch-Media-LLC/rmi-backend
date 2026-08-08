"""Core scanner module — shared constants and helpers.

All RPC calls now route through the unified RpcRouter (rpc_router.py)
which maintains 8-15 endpoints per chain with automatic health checks
and fallback. No file hardcodes its own RPC URLs anymore.

Own-Data Integration:
    _fetch_dexscreener now tries ClickHouse own-dex-data FIRST when
    PREFER_OWN_DATA=true (default), falling back to DexScreener API only
    when our data has < 3 swaps. This eliminates 111 DexScreener API calls
    per token scan.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger
from app.domains.scanner.engines.rpc_router import get_rpc_router

logger = get_logger(__name__)

# ── Feature flag: prefer own ClickHouse data over external APIs ──
PREFER_OWN_DATA = os.getenv("PREFER_OWN_DATA", "true").lower() in ("true", "1", "yes")

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

# ── Unified RPC Router (EVM + Solana) ─────────────────────────────────
# RPC calls now route through RpcRouter with 8-15 endpoints per chain
# and automatic health checks. See rpc_router.py for endpoint lists.
#
# Convenience wrappers below for backward compatibility.

SOLANA_RPC_TIMEOUT = 10.0


async def _solana_rpc_call(method: str, params: list[Any]) -> dict[str, Any] | None:
    """Call Solana RPC with full fallback across all public endpoints.

    Routes through the unified RpcRouter which maintains health checks
    for each endpoint and skips dead/rate-limited endpoints automatically.

    For the full list of Solana endpoints, see:
        app/domains/scanner/engines/rpc_router.SOLANA_RPC_ENDPOINTS
    """
    router = get_rpc_router()
    result = await router.call("solana", method, params, timeout=SOLANA_RPC_TIMEOUT)
    return result


async def _evm_rpc_call(chain: str, method: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    """Call any EVM chain RPC with full fallback across all public endpoints.

    Routes through the unified RpcRouter which tries 8-15 endpoints per chain
    with automatic health checks, latency tracking, and dead-endpoint skipping.

    Supported chains with expanded endpoints:
        ethereum  (15 endpoints), bsc  (16 endpoints),
        polygon   ( 9 endpoints), base   ( 9 endpoints),
        arbitrum  ( 9 endpoints), optimism (9 endpoints),
        avalanche ( 9 endpoints), fantom (8 endpoints),
        linea     ( 7 endpoints), zksync (6 endpoints),
        scroll    ( 6 endpoints), mantle (6 endpoints)
    """
    router = get_rpc_router()
    return await router.call(chain, method, params)


async def _evm_eth_call(chain: str, to_address: str, data: str) -> str | None:
    """Convenience: eth_call on any EVM chain via the RPC router."""
    router = get_rpc_router()
    return await router.eth_call(chain, to_address, data)


async def _evm_get_logs(
    chain: str,
    address: str,
    from_block: str,
    to_block: str,
    topics: list[str] | None = None,
) -> list[dict[str, Any]] | None:
    """Convenience: eth_getLogs on any EVM chain via the RPC router."""
    router = get_rpc_router()
    return await router.eth_get_logs(chain, address, from_block, to_block, topics)


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
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
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
            except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
                logger.debug(f"failed {url}: {e}")
                continue
    return {}, ""


# ── 1. GoPlus Security ────────────────────────────────────────────────

# ── DexScreener request cache (prevents N redundant calls per scan) ──
# Multiple scanner modules (_check_honeypot_is, _get_holder_data,
# _check_liquidity_lock_multi, _get_deployer_info) all call DexScreener
# for the same token. This LRU cache deduplicates within a process.
_DEXSCREENER_CACHE: dict[str, dict[str, Any] | None] = {}  # token_address -> response
_DEXSCREENER_CACHE_MAX = 100


async def _fetch_dexscreener(
    token_address: str,
    chain: str = "ethereum",
) -> dict[str, Any] | None:
    """Fetch DexScreener data once per token, cached per process.

    When PREFER_OWN_DATA=true (default), tries ClickHouse own-dex-data first,
    falling back to the DexScreener API only if ClickHouse has < 3 swaps.

    All scanner modules should call this instead of crafting their own
    DexScreener URL.  The cache lives for the lifetime of the process
    (typically minutes per scan request before GC), which is fine since
    DexScreener data doesn't change intra-second.
    """
    key = f"{token_address.lower()}:{chain}"
    if key in _DEXSCREENER_CACHE:
        return _DEXSCREENER_CACHE[key]

    # ── Tier 1: Own ClickHouse data (when feature flag enabled) ──
    if PREFER_OWN_DATA:
        try:
            from app.domains.scanner.engines.own_dex_data import get_own_dex_data

            dex_result = await get_own_dex_data(token_address, chain)
            if dex_result.get("source") == "own" and dex_result.get("pairs"):
                pairs = dex_result["pairs"]
                result = {
                    "pairs": [
                        {
                            "chainId": p.get("chainId", ""),
                            "dexId": p.get("dexId", ""),
                            "pairAddress": p.get("pairAddress", ""),
                            "priceUsd": p.get("priceUsd", "0"),
                            "volume": p.get("volume", {}),
                            "liquidity": p.get("liquidity", {}),
                            "txns": p.get("txns", {}),
                            "priceChange": p.get("priceChange", {}),
                            "fdv": p.get("fdv", 0),
                            "pairCreatedAt": p.get("pairCreatedAt", 0),
                            "holders": p.get("holders", ""),
                            "labels": p.get("labels", []),
                        }
                        for p in pairs
                    ],
                    "pairCount": len(pairs),
                }
                logger.info(
                    "Using own DEX data for %s on %s",
                    token_address[:16],
                    chain,
                )
                # Cache and return
                if len(_DEXSCREENER_CACHE) >= _DEXSCREENER_CACHE_MAX:
                    _DEXSCREENER_CACHE.pop(next(iter(_DEXSCREENER_CACHE)), None)
                _DEXSCREENER_CACHE[key] = result
                return result

            logger.debug(
                "Falling back to DexScreener API for %s (own data: %s)",
                token_address[:16],
                dex_result.get("source", "unavailable"),
            )
        except ImportError:
            logger.debug(
                "own_dex_data module not available, using DexScreener API for %s",
                token_address[:16],
            )
        except Exception as exc:
            logger.warning(
                "own_dex_data failed for %s: %s",
                token_address[:16],
                exc,
            )
    else:
        logger.debug(
            "PREFER_OWN_DATA=false, using DexScreener API for %s",
            token_address[:16],
        )

    # ── Tier 2: DexScreener API (original behavior) ──
    data, _ = await _fetch_with_fallback(
        [f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"],
    )
    result = data if data and data.get("pairs") else None

    # Evict oldest if over limit
    if len(_DEXSCREENER_CACHE) >= _DEXSCREENER_CACHE_MAX:
        _DEXSCREENER_CACHE.pop(next(iter(_DEXSCREENER_CACHE)), None)
    _DEXSCREENER_CACHE[key] = result

    return result
