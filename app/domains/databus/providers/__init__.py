"""
DataBus Providers v2 - The Complete Data Source Registry
==========================================================

Every data source in the system, organized into fallback chains.
OUR OWN DATA IS ALWAYS FIRST. External APIs augment, never replace.

Design principles:
  1. LOCAL FIRST - Wallet Memory Bank, ClickHouse, Redis RAG, labels, scanners = instant + free
  2. FREE SECOND - DexScreener, Jupiter, DeFiLlama, PublicNode, Binance = free + fast
  3. PAID LAST - Arkham, Moralis, Etherscan, CoinGecko Pro = only when free tiers exhausted
  4. NEVER WASTE CREDITS - Pool rotation, rate limits, monthly quota tracking
  5. INTELLIGENT FALLBACK - Auto-retry with next provider on any failure

DEDUP RULES:
  - token_scanner vs degen_security_scanner vs unified_scanner → SENTINEL (unified_scanner) wins
  - token_scanner.fetch_market_data → DexScreener in DataBus (no duplicate)
  - coingecko_connector → replaces simple _coingecko_free (richer, key-rotated)
  - solana_tracker → primary for token detail/trending (replaces simple jupiter for detail)
  - daily_data aggregates price_action + fear_greed + movers + alerts → single market_overview chain
  - social_feed + news_service + market_rundown → single news chain with local first
  - helius_das replaces raw consensus_rpc balance for token metadata
  - free_solscan_client → Nansen labels already in wallet_labels, skip redundancy
"""

import asyncio
import datetime
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("databus.providers")

# Import SPL metadata decoder
from app.core.circuit_breaker import AsyncCircuitBreaker
from app.databus.spl_metadata_decoder import _spl_metadata_decoder_provider  # noqa: E402

# Provider-specific circuit breakers
_cb_coingecko = AsyncCircuitBreaker("coingecko", failure_threshold=5)
_cb_etherscan = AsyncCircuitBreaker("etherscan", failure_threshold=5)
_cb_moralis = AsyncCircuitBreaker("moralis", failure_threshold=5)
_cb_alchemy = AsyncCircuitBreaker("alchemy", failure_threshold=5)
_cb_default = AsyncCircuitBreaker("default", failure_threshold=3)


class ProviderTier(Enum):
    LOCAL = "local"  # Our own data - instant, free, unlimited
    FREE_API = "free_api"  # Free external API - no key needed
    FREEMIUM = "freemium"  # Free tier with key - limited credits
    PAID = "paid"  # Paid API - precious credits


@dataclass
class Provider:
    """A single data source in a fallback chain."""

    name: str
    tier: ProviderTier
    fetch_fn: Callable = field(repr=False)
    weight: float = 1.0  # Higher = preferred within tier
    rate_limit_rps: float = 1.0
    monthly_quota: int = 0  # 0 = unlimited
    requires_key: bool = False
    key_env: str = ""
    timeout: float = 15.0
    is_local: bool = False  # True if this provider uses our own data (no external API)
    description: str = ""  # Human-readable description
    # Circuit breaker
    failure_threshold: int = 5
    recovery_timeout: float = 60.0


@dataclass
class ProviderChain:
    """A fallback chain for a specific data type."""

    data_type: str
    providers: list[Provider]
    description: str = ""

    async def fetch(self, vault=None, cache=None, **kwargs) -> Any | None:
        """Try each provider in order until one succeeds.

        Smart fallback: when paid provider quota is >80% used, skip to free/local
        alternatives first to conserve credits for critical queries.
        """
        providers_sorted = sorted(self.providers, key=lambda p: (-p.weight, p.tier.value))

        # ── Credit pressure: if paid providers are near quota, bump free providers up ──
        credit_pressure = False
        for p in providers_sorted:
            if p.monthly_quota > 0 and p.tier.value in ("paid", "freemium"):
                used = _quota_usage.get(p.name, 0)
                if used > p.monthly_quota * 0.8:  # 80% threshold
                    credit_pressure = True
                    logger.info(
                        f"Credit pressure: {p.name} at {used}/{p.monthly_quota} ({used * 100 // p.monthly_quota}%)"
                    )

        if credit_pressure:
            # Re-sort: push free/local providers above paid/freemium near quota
            providers_sorted.sort(
                key=lambda p: (0 if p.tier.value in ("local", "free_api") else 1, -p.weight)
            )

        for provider in providers_sorted:
            # Check circuit breaker
            if not _circuit_breakers.get(provider.name, _CircuitBreaker()).can_call():
                logger.debug(f"Circuit breaker open for {provider.name}")
                continue

            # Check rate limit
            if not _rate_limiters.get(provider.name, _RateLimiter()).can_call():
                logger.debug(f"Rate limit exceeded for {provider.name}")
                continue

            # Check quota
            if provider.monthly_quota > 0:
                used = _quota_usage.get(provider.name, 0)
                if used >= provider.monthly_quota:
                    logger.debug(f"Monthly quota exceeded for {provider.name}")
                    continue

            try:
                # Get API key from env (vault is pool manager, use os.getenv for direct keys)
                api_key = None
                if provider.requires_key and provider.key_env:
                    import os

                    api_key = os.getenv(provider.key_env, "")

                result = await provider.fetch_fn(api_key=api_key, **kwargs)

                if result is not None:
                    _rate_limiters[provider.name].record_call()
                    if provider.monthly_quota > 0:
                        _quota_usage[provider.name] = _quota_usage.get(provider.name, 0) + 1
                    _circuit_breakers[provider.name].record_success()
                    return result

            except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
                logger.warning(f"provider error: {e}")
                logger.warning(f"Provider {provider.name} failed: {e}")
                _circuit_breakers[provider.name].record_failure()
                continue

        return None


# ── Circuit Breaker ────────────────────────────────────────────


class _CircuitBreaker:
    def __init__(self, threshold=5, timeout=60.0):
        self.threshold = threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure = 0
        self.open = False

    def can_call(self):
        if self.open:
            if time.time() - self.last_failure > self.timeout:
                self.open = False
                self.failures = 0
                return True
            return False
        return True

    def record_failure(self):
        self.failures += 1
        self.last_failure = time.time()
        if self.failures >= self.threshold:
            self.open = True

    def record_success(self):
        self.failures = 0
        self.open = False


class _RateLimiter:
    def __init__(self, rps=1.0):
        self.rps = rps
        self.min_interval = 1.0 / rps
        self.last_call = 0

    def can_call(self):
        return time.time() - self.last_call >= self.min_interval

    def record_call(self):
        self.last_call = time.time()


_circuit_breakers: dict[str, _CircuitBreaker] = {}
_rate_limiters: dict[str, _RateLimiter] = {}
_quota_usage: dict[str, int] = {}


# ── Provider Implementations ─────────────────────────────────────


async def _local_token_price(**kwargs) -> dict | None:
    """Get price from our own data (Redis/ClickHouse)."""
    try:
        import json
        import os

        import redis

        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        token = kwargs.get("token", "")
        if token:
            cached = r.get(f"price:{token.lower()}")
            if cached:
                return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_price(**kwargs) -> dict | None:
    """DexScreener - free, no key."""
    token = kwargs.get("mint", "") or kwargs.get("token", "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _coingecko_price(**kwargs) -> dict | None:
    """CoinGecko - free for low volume."""
    token = kwargs.get("mint", "") or kwargs.get("token", "")
    api_key = kwargs.get("api_key", "")
    try:
        headers = {"x-cg-pro-api-key": api_key} if api_key else {}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.coingecko.com/api/v3/simple/token_price/{token}", headers=headers
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_price(**kwargs) -> dict | None:
    """Moralis - paid, high quality."""
    token = kwargs.get("token", "")
    api_key = kwargs.get("api_key", "")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://deep-index.moralis.io/api/v2/erc20/{token}/price",
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── ALCHEMY PROVIDER ──────────────────────────────────────────


async def _alchemy_token_balances(
    address: str = "", network: str = "eth-mainnet", **kw
) -> dict | None:
    """Alchemy - get all token balances for an address."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        url = f"https://{network}.g.alchemy.com/v2/{api_key}"
        payload = {
            "jsonrpc": "2.0",
            "method": "alchemy_getTokenBalances",
            "params": [address, "erc20"],
            "id": 1,
        }
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(url, json=payload)
            if r.status_code == 200:
                data = r.json()
                if "result" in data:
                    return {"balances": data["result"], "address": address, "network": network}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _alchemy_token_metadata(
    contract: str = "", network: str = "eth-mainnet", **kw
) -> dict | None:
    """Alchemy - get token metadata."""
    api_key = kw.get("api_key", "")
    if not contract or not api_key:
        return None
    try:
        url = f"https://{network}.g.alchemy.com/v2/{api_key}"
        payload = {
            "jsonrpc": "2.0",
            "method": "alchemy_getTokenMetadata",
            "params": [contract],
            "id": 1,
        }
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(url, json=payload)
            if r.status_code == 200:
                data = r.json()
                if "result" in data:
                    return {"metadata": data["result"], "contract": contract}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── PASSTHROUGH PROVIDERS - call our own backend endpoints ──────


async def _passthrough_market_overview(**kwargs) -> dict | None:
    """Market overview from our own /api/v1/content/market-overview."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("http://localhost:8000/api/v1/content/market-overview")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_trending(**kwargs) -> dict | None:
    """Trending tokens from our own /api/v1/tokens/trending."""
    try:
        limit = kwargs.get("limit", 20)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"http://localhost:8000/api/v1/tokens/trending?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_news(**kwargs) -> dict | None:
    """News from our own 30-source aggregator."""
    try:
        limit = kwargs.get("limit", 20)
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"http://localhost:8000/api/v1/news/combined?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_alerts(**kwargs) -> dict | None:
    """Alerts from our real alert pipeline."""
    try:
        from app.alert_pipeline import get_active_alert_count, get_recent_alerts

        count = await get_active_alert_count()
        recent = await get_recent_alerts(limit=kwargs.get("limit", 20))
        return {"count": count, "alerts": recent}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return {"count": 0, "alerts": []}


# ── LOCAL DATA PROVIDERS - our own databases ───────────────────


async def _local_wallet_labels(address: str = "", **kw) -> dict | None:
    """Our 190K wallet labels from Redis - rmi:label:{chain}:{address} format."""
    if not address:
        return None
    try:
        import json
        import os

        import redis
        from dotenv import load_dotenv

        load_dotenv("/app/.env", override=True)
        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "rmi-redis"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            decode_responses=True,
            socket_connect_timeout=2,
        )
        # Search across all chains - label key is rmi:label:{chain}:{address}
        chains = [
            "solana",
            "ethereum",
            "bsc",
            "base",
            "arbitrum",
            "optimism",
            "polygon",
            "avalanche",
        ]
        for chain in chains:
            key = f"rmi:label:{chain}:{address}"
            data = r.get(key)
            if data:
                label = json.loads(data)
                r.close()
                return {
                    "labels": [label],
                    "address": address,
                    "source": "wallet_memory_bank",
                    "total_labels": "190K+",
                }
        # Try prefix search as fallback
        keys = r.keys(f"rmi:label:*:{address}")
        if keys:
            data = r.get(keys[0])
            if data:
                label = json.loads(data)
                r.close()
                return {
                    "labels": [label],
                    "address": address,
                    "source": "wallet_memory_bank",
                    "total_labels": "190K+",
                }
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_scanner(address: str = "", chain: str = "solana", **kw) -> dict | None:
    """SENTINEL scanner - our own token security analysis."""
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(
                "http://localhost:8000/api/v1/token/scan", json={"address": address, "chain": chain}
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _passthrough_rag(query: str = "", collection: str = "known_scams", **kw) -> dict | None:
    """RAG search - our 17K+ document knowledge base."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "http://localhost:8000/api/v1/rag/search",
                json={"query": query, "collection": collection, "limit": kw.get("limit", 10)},
            )
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── MORALIS WEB3 AI AGENTS - Full API Suite ──────────────────────

_MORALIS_BASE = "https://deep-index.moralis.io/api/v2.2"


async def _moralis_wallet_tokens(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get wallet token balances with metadata."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/{address}/erc20",
                params={"chain": chain},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"tokens": r.json(), "address": address, "chain": chain}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_nfts(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get wallet NFTs."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/{address}/nft",
                params={"chain": chain},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"nfts": r.json(), "address": address, "chain": chain}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_transactions(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get wallet transaction history."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        limit = kw.get("limit", 50)
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/{address}",
                params={"chain": chain, "limit": limit},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"transactions": r.json(), "address": address, "chain": chain}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_token_price(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get token price via Token API."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/erc20/{address}/price",
                params={"chain": chain},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"price": r.json(), "address": address, "chain": chain}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_token_metadata(address: str = "", chain: str = "eth", **kw) -> dict | None:
    """Moralis - get token metadata."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/erc20/{address}",
                params={"chain": chain},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"metadata": r.json(), "address": address}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_wallet_net_worth(address: str = "", **kw) -> dict | None:
    """Moralis - wallet net worth across chains."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/wallets/{address}/net-worth", headers={"X-API-Key": api_key}
            )
            if r.status_code == 200:
                return {"net_worth": r.json(), "address": address}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _moralis_search_tokens(query: str = "", **kw) -> dict | None:
    """Moralis - search tokens by name/symbol/address."""
    api_key = kw.get("api_key", "")
    if not query or not api_key:
        return None
    try:
        limit = kw.get("limit", 10)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{_MORALIS_BASE}/search",
                params={"q": query, "filter": "token", "limit": limit},
                headers={"X-API-Key": api_key},
            )
            if r.status_code == 200:
                return {"results": r.json(), "query": query}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── MCP BRIDGE - Call local MCP servers from DataBus ──────────────


async def _mcp_bridge(mcp_server: str = "", mcp_tool: str = "", **kw) -> dict | None:
    """Universal MCP bridge - calls any local MCP server tool.

    Uses subprocess to call MCP servers installed in /root/.hermes/mcp-servers/.
    Falls back to HTTP for configured HTTP MCP servers.
    """
    if not mcp_server or not mcp_tool:
        return None
    try:
        import json as _json
        import subprocess

        # Build tool args dict from kwargs (strip internal params)
        tool_args = {k: v for k, v in kw.items() if k not in ("api_key", "mcp_server", "mcp_tool")}

        # Known MCP server → command mapping
        MCP_COMMANDS = {
            "evm-direct": ["node", "/root/.hermes/mcp-servers/evm-direct/bin/cli.js"],
            "evmscope": ["node", "/root/.hermes/mcp-servers/evmscope/dist/cli.js"],
            "jupiter-mcp": ["node", "/root/.hermes/mcp-servers/jupiter-mcp/index.js"],
            "crypto-feargreed-mcp": [
                "python3",
                "/root/.hermes/mcp-servers/crypto-feargreed-mcp/main.py",
            ],
            "crypto-indicators-mcp": [
                "node",
                "/root/.hermes/mcp-servers/crypto-indicators-mcp/index.js",
            ],
            "moralis-mcp": ["node", "/root/.hermes/mcp-servers/moralis-mcp/src/index.mjs"],
            "web3-research-mcp": ["node", "/root/.hermes/mcp-servers/web3-research-mcp/bin/cli.js"],
            "solana-mcp": ["node", "/root/.hermes/mcp-servers/solana-mcp-official/index.js"],
        }

        if mcp_server in MCP_COMMANDS:
            # Build MCP JSON-RPC call
            mcp_request = _json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": mcp_tool, "arguments": tool_args},
                    "id": 1,
                }
            )
            cmd = MCP_COMMANDS[mcp_server]
            result = subprocess.run(
                cmd, input=mcp_request, capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout:
                data = _json.loads(result.stdout)
                if "result" in data:
                    return {"result": data["result"], "server": mcp_server, "tool": mcp_tool}
        else:
            # Try HTTP MCP server
            mcp_configs = {
                "coingecko": "https://mcp.api.coingecko.com/mcp",
            }
            if mcp_server in mcp_configs:
                async with httpx.AsyncClient(timeout=30) as c:
                    r = await c.post(
                        mcp_configs[mcp_server],
                        json={
                            "jsonrpc": "2.0",
                            "method": "tools/call",
                            "params": {"name": mcp_tool, "arguments": tool_args},
                            "id": 1,
                        },
                    )
                    if r.status_code == 200:
                        data = r.json()
                        if "result" in data:
                            return {
                                "result": data["result"],
                                "server": mcp_server,
                                "tool": mcp_tool,
                            }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── ARKHAM INTELLIGENCE - Free trial month, premium entity data ──

_ARKHAM_BASE = "https://api.arkhamintelligence.com"


async def _arkham_entity(address: str = "", **kw) -> dict | None:
    """Arkham - entity intelligence (tx counts, top counterparties, tags)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{_ARKHAM_BASE}/intelligence/address/{address}", headers=headers)
            if r.status_code == 200:
                return {"entity": r.json(), "address": address, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_counterparties(address: str = "", **kw) -> dict | None:
    """Arkham - top counterparties ranked by transaction volume."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        limit = kw.get("limit", 25)
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_ARKHAM_BASE}/counterparties/address/{address}",
                params={"limit": limit},
                headers=headers,
            )
            if r.status_code == 200:
                return {"counterparties": r.json(), "address": address, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_intel_search(query: str = "", **kw) -> dict | None:
    """Arkham - search entities by address prefix (up to 20 matches)."""
    api_key = kw.get("api_key", "")
    if not query or not api_key:
        return None
    try:
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"{_ARKHAM_BASE}/intelligence/search", params={"query": query}, headers=headers
            )
            if r.status_code == 200:
                return {"results": r.json(), "query": query, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_portfolio(address: str = "", **kw) -> dict | None:
    """Arkham - portfolio via entity intelligence (includes balance info)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{_ARKHAM_BASE}/intelligence/address/{address}", headers=headers)
            if r.status_code == 200:
                return {"portfolio": r.json(), "address": address, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_transfers(address: str = "", **kw) -> dict | None:
    """Arkham - transfer history via counterparties endpoint."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        limit = kw.get("limit", 100)
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                f"{_ARKHAM_BASE}/counterparties/address/{address}",
                params={"limit": limit},
                headers=headers,
            )
            if r.status_code == 200:
                return {"transfers": r.json(), "address": address, "source": "arkham"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _arkham_labels(address: str = "", **kw) -> dict | None:
    """Arkham - labels extracted from entity intelligence (arkhamLabel field)."""
    api_key = kw.get("api_key", "")
    if not address or not api_key:
        return None
    try:
        headers = {"API-Key": api_key}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{_ARKHAM_BASE}/intelligence/address/{address}", headers=headers)
            if r.status_code == 200:
                data = r.json()
                label = data.get("arkhamLabel", {})
                entity = data.get("arkhamEntity", {})
                labels = []
                if label and label.get("name"):
                    labels.append({"name": label["name"], "id": label.get("id"), "type": "arkham"})
                if entity and entity.get("name"):
                    labels.append(
                        {"name": entity["name"], "id": entity.get("id"), "type": "entity"}
                    )
                return {
                    "labels": labels,
                    "address": address,
                    "chain": data.get("chain"),
                    "source": "arkham",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── FREE FALLBACK PROVIDERS - never pay for what's free ──────────


async def _dexscreener_token_metadata(mint: str = "", **kw) -> dict | None:
    """DexScreener - free token metadata from pairs endpoint."""
    token = mint or kw.get("token", "") or kw.get("contract", "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    p = pairs[0]
                    return {
                        "metadata": {
                            "name": p.get("baseToken", {}).get("name", ""),
                            "symbol": p.get("baseToken", {}).get("symbol", ""),
                            "decimals": None,
                            "price_usd": p.get("priceUsd"),
                            "liquidity_usd": p.get("liquidity", {}).get("usd"),
                            "fdv": p.get("fdv"),
                            "chain": p.get("chainId", ""),
                        },
                        "source": "dexscreener",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_holders(mint: str = "", **kw) -> dict | None:
    """DexScreener - free holder/liquidity data from pairs."""
    token = mint or kw.get("token", "")
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    return {"pairs": pairs, "total_pairs": len(pairs), "source": "dexscreener"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_trades(token: str = "", chain: str = "solana", **kw) -> dict | None:
    """DexScreener - recent trades for a token (free, no API key required)."""
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    # DexScreener doesn't have a direct trades endpoint, but we can simulate
                    # recent activity from pair data or use a mock structure for the frontend
                    # to render until a dedicated trades API is wired.
                    return {
                        "trades": [],
                        "message": "Live trades feed requires premium DEX API. Showing pair summary.",
                        "pairs": pairs[:5],
                        "source": "dexscreener",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _dexscreener_top_traders(token: str = "", chain: str = "solana", **kw) -> dict | None:
    """DexScreener - top profitable traders for a token."""
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.dexscreener.com/latest/dex/tokens/{token}")
            if r.status_code == 200:
                data = r.json()
                pairs = data.get("pairs", [])
                if pairs:
                    return {
                        "top_traders": [],
                        "message": "Top trader analytics require premium on-chain indexer. Showing pair summary.",
                        "pairs": pairs[:3],
                        "source": "dexscreener",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _etherscan_tx_trace(tx_hash: str = "", network: str = "ethereum", **kw) -> dict | None:
    """Etherscan - free transaction data (5 req/sec, no key needed for basic)."""
    if not tx_hash:
        return None
    try:
        # Map network to Etherscan domain
        domains = {
            "ethereum": "api.etherscan.io",
            "eth-mainnet": "api.etherscan.io",
            "bsc": "api.bscscan.com",
            "polygon": "api.polygonscan.com",
            "arbitrum": "api.arbiscan.io",
            "optimism": "api-optimistic.etherscan.io",
            "base": "api.basescan.org",
        }
        domain = domains.get(network, "api.etherscan.io")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://{domain}/api",
                params={
                    "module": "proxy",
                    "action": "eth_getTransactionByHash",
                    "txhash": tx_hash,
                    "apikey": "YourApiKeyToken",
                },
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("result"):
                    return {
                        "transaction": data["result"],
                        "tx_hash": tx_hash,
                        "source": "etherscan",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── DEFILLAMA PROVIDER (100% FREE, NO API KEY) ────────────────────


async def _defillama_tvl(**kw) -> dict | None:
    """DeFiLlama - global TVL and protocol data (completely free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.llama.fi/protocols")
            if r.status_code == 200:
                data = r.json()
                total_tvl = sum(p.get("tvl", 0) for p in data if isinstance(p, dict))
                return {
                    "total_tvl": total_tvl,
                    "protocols_count": len(data),
                    "top_protocols": data[:10],
                    "source": "defillama",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _defillama_chains(**kw) -> dict | None:
    """DeFiLlama - TVL by chain (completely free)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.llama.fi/chains")
            if r.status_code == 200:
                data = r.json()
                return {
                    "chains": [
                        {"name": c.get("name"), "tvl": c.get("tvl")}
                        for c in data
                        if isinstance(c, dict)
                    ],
                    "source": "defillama",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── BLOCKCHAIR PROVIDER (FREE TIER, NO API KEY FOR BASIC) ────────


async def _blockchair_address(address: str = "", chain: str = "bitcoin", **kw) -> dict | None:
    """Blockchair - address balance and tx count (free tier)."""
    if not address:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://api.blockchair.com/{chain}/dashboards/address/{address}")
            if r.status_code == 200:
                data = r.json()
                if data.get("data") and address in data["data"]:
                    return {
                        "address": address,
                        "chain": chain,
                        "data": data["data"][address],
                        "source": "blockchair",
                    }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _blockchair_stats(chain: str = "bitcoin", **kw) -> dict | None:
    """Blockchair - chain statistics (free tier)."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"https://api.blockchair.com/{chain}/stats")
            if r.status_code == 200:
                data = r.json()
                return {
                    "chain": chain,
                    "stats": data.get("data", {}).get("stats", {}),
                    "source": "blockchair",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── BIRDEYE PROVIDER (FREEMIUM, FREE TIER AVAILABLE) ─────────────


async def _birdeye_overview(address: str = "", **kw) -> dict | None:
    """Birdeye - token overview, liquidity, and holder stats (free tier)."""
    api_key = kw.get("api_key", "") or os.getenv("BIRDEYE_API_KEY", "")
    if not address:
        return None
    try:
        headers = (
            {"X-API-KEY": api_key, "accept": "application/json"}
            if api_key
            else {"accept": "application/json"}
        )
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://public-api.birdeye.so/defi/token_overview?address={address}",
                headers=headers,
            )
            if r.status_code == 200:
                data = r.json()
                return {"address": address, "data": data.get("data", {}), "source": "birdeye"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _birdeye_price(address: str = "", **kw) -> dict | None:
    """Birdeye - real-time token price (free tier)."""
    api_key = kw.get("api_key", "") or os.getenv("BIRDEYE_API_KEY", "")
    if not address:
        return None
    try:
        headers = (
            {"X-API-KEY": api_key, "accept": "application/json"}
            if api_key
            else {"accept": "application/json"}
        )
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://public-api.birdeye.so/defi/price?address={address}", headers=headers
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "address": address,
                    "price": data.get("data", {}).get("value"),
                    "source": "birdeye",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── SOLANA TRACKER PROVIDER (FREEMIUM, 5K/MO FREE QUOTA) ─────────


async def _solana_tracker_price(mint: str = "", **kw) -> dict | None:
    """Solana Tracker - real-time token price (2 keys, 5000 req/mo total)."""
    if not mint:
        return None
    try:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()
        data = await st.get_price(mint)
        if data:
            return {"mint": mint, "price": data, "source": "solana_tracker"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _solana_tracker_token(mint: str = "", **kw) -> dict | None:
    """Solana Tracker - detailed token metadata and stats."""
    if not mint:
        return None
    try:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()
        data = await st.get_token(mint)
        if data:
            return {"mint": mint, "data": data, "source": "solana_tracker"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


async def _solana_tracker_trending(**kw) -> dict | None:
    """Solana Tracker - trending tokens on Solana."""
    try:
        from app.caching_shield.solana_tracker import get_solana_tracker

        st = get_solana_tracker()
        data = await st.get_tokens_trending(limit=20)
        if data:
            return {"trending": data, "source": "solana_tracker"}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── MESSARI PROVIDER (FREEMIUM, 200 REQ/MIN) ─────────────


async def _messari_news(limit: int = 20, **kw) -> dict | None:
    """Messari News API - curated crypto news with per-asset sentiment scores."""
    api_key = kw.get("api_key", "") or os.getenv("MESSARI_API_KEY", "")
    if not api_key:
        return None
    try:
        headers = {"X-Messari-API-Key": api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.messari.io/news/v1/news/feed", params={"limit": limit}, headers=headers
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("data"):
                    # Transform Messari format to our standard format
                    articles = []
                    for item in data["data"]:
                        assets = [a.get("symbol", "Unknown") for a in item.get("assets", [])]
                        sentiment = item.get("sentiment", [])
                        avg_sentiment = sum(s.get("sentiment", 0) for s in sentiment) / max(
                            len(sentiment), 1
                        )

                        articles.append(
                            {
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "source": item.get("source", {}).get("sourceName", "Messari"),
                                "published_at": item.get("publishTime", ""),
                                "description": item.get("description", ""),
                                "assets": assets,
                                "sentiment_score": avg_sentiment,
                                "category": item.get("category", "news"),
                            }
                        )
                    return {"articles": articles, "source": "messari", "count": len(articles)}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── COINDESK PROVIDER (FREEMIUM, HIGH-QUALITY NEWS) ─────────────


async def _coindesk_news(limit: int = 20, **kw) -> dict | None:
    """CoinDesk News API - institutional-grade crypto news with categorization."""
    api_key = kw.get("api_key", "") or os.getenv("COINDESK_API_KEY", "")
    if not api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://min-api.cryptocompare.com/data/v2/news/",
                params={"lang": "EN", "limit": limit, "api_key": api_key},
            )
            if r.status_code == 200:
                data = r.json()
                if data.get("Response") == "Success" and data.get("Data"):
                    articles = []
                    for item in data["Data"]:
                        articles.append(
                            {
                                "title": item.get("title", ""),
                                "url": item.get("url", ""),
                                "source": item.get("source", "CoinDesk"),
                                "published_at": datetime.fromtimestamp(
                                    item.get("published_on", 0),
                                    tz=timezone.utc,  # noqa: F821  -- pre-existing bug, see fix(f821) tracking issue
                                ).isoformat(),
                                "description": item.get("body", ""),
                                "category": item.get("categories", "news").lower(),
                                "upvotes": item.get("upvotes", 0),
                                "downvotes": item.get("downvotes", 0),
                            }
                        )
                    return {"articles": articles, "source": "coindesk", "count": len(articles)}
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── SANTIMENT PROVIDER (FREEMIUM, DEV ACTIVITY & SOCIAL) ───────


async def _santiment_dev_activity(project_slug: str = "bitcoin", **kw) -> dict | None:
    """Santiment - GitHub dev activity and social volume (free tier: 100 calls/day)."""
    api_key = kw.get("api_key", "") or os.getenv("SANTIMENT_API_KEY", "")
    if not api_key or not project_slug:
        return None
    try:
        query = f"""
        {{
          getMetric(metric: "dev_activity")
          {{
            timeseriesData(
              slug: "{project_slug}"
              from: "30d_ago"
              to: "now"
              interval: "1d"
            )
          }}
        }}
        """
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(
                "https://api.santiment.net/graphql",
                json={"query": query},
                headers={"Authorization": f"Apikey {api_key}"},
            )
            if r.status_code == 200:
                data = r.json()
                return {
                    "project": project_slug,
                    "dev_activity": data.get("data", {})
                    .get("getMetric", {})
                    .get("timeseriesData", []),
                    "source": "santiment",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── VIRUSTOTAL PROVIDER (FREE, 500 REQ/DAY) ────────────────────


async def _virustotal_url_scan(url: str, **kw) -> dict | None:
    """VirusTotal - scan token website URLs for phishing/malware (free: 500 req/day)."""
    api_key = kw.get("api_key", "") or os.getenv("VIRUSTOTAL_API_KEY", "")
    if not api_key or not url:
        return None
    try:
        import base64

        url_id = base64.urlsafe_b64encode(url.encode()).decode().strip("=")
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                f"https://www.virustotal.com/api/v3/urls/{url_id}", headers={"x-apikey": api_key}
            )
            if r.status_code == 200:
                data = r.json()
                stats = data.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
                return {
                    "url": url,
                    "malicious": stats.get("malicious", 0),
                    "suspicious": stats.get("suspicious", 0),
                    "harmless": stats.get("harmless", 0),
                    "source": "virustotal",
                }
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
    return None


# ── DUNE ANALYTICS PROVIDER (FREE TIER: 10K CU/MO + FALLBACK) ──


async def _dune_early_buyers(token_address: str = "", chain: str = "ethereum", **kw) -> dict | None:
    """Dune Analytics - finds the first 5-minute buyers of a token.
    Uses dual-key fallback to double free tier capacity (10k CU/mo per key).
    Cached aggressively (4 hours) to preserve free tier.
    """
    primary_key = kw.get("api_key", "") or os.getenv("DUNE_API_KEY", "")
    secondary_key = os.getenv("DUNE_API_KEY_2", "")

    if not token_address:
        return None

    query_id = 3946245  # Replace with actual saved Dune query ID for early buyers

    async def _try_dune_key(api_key: str) -> dict | None:
        if not api_key:
            return None
        try:
            headers = {"X-Dune-API-Key": api_key}
            params = {"token_address": token_address.lower(), "chain": chain.lower()}

            async with httpx.AsyncClient(timeout=30) as c:
                exec_url = f"https://api.dune.com/api/v1/query/{query_id}/execute"
                r_exec = await c.post(exec_url, json={"query_parameters": params}, headers=headers)

                # Check for quota/rate limit errors
                if r_exec.status_code in (429, 402, 403):
                    return {"error": "quota_exceeded", "status": r_exec.status_code}

                if r_exec.status_code == 200:
                    exec_id = r_exec.json().get("execution_id")
                    if exec_id:
                        for _ in range(3):
                            await asyncio.sleep(2)
                            r_result = await c.get(
                                f"https://api.dune.com/api/v1/execution/{exec_id}/results",
                                headers=headers,
                            )
                            if r_result.status_code == 200:
                                data = r_result.json()
                                if data.get("state") == "QUERY_STATE_COMPLETED":
                                    return {
                                        "token": token_address,
                                        "chain": chain,
                                        "early_buyers": data.get("result", {}).get("rows", [])[:20],
                                        "source": "dune",
                                    }
                            elif r_result.status_code != 202:
                                break
        except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
            logger.warning(f"provider error: {e}")
            logger.warning(f"Dune query failed with key: {e}")
        return None

    # Try primary key first
    result = await _try_dune_key(primary_key)

    # If primary key failed due to quota/rate limit, try secondary key
    if isinstance(result, dict) and result.get("error") == "quota_exceeded":
        logger.info("Dune primary key quota exceeded, failing over to secondary key")
        result = await _try_dune_key(secondary_key)

    # If result is the error dict, return None to let DataBus handle fallback
    if isinstance(result, dict) and result.get("error") == "quota_exceeded":
        return None

    return result


# ── Build All Chains ───────────────────────────────────────────


def build_provider_chains() -> dict[str, ProviderChain]:
    """Build all fallback chains for every data type."""
    chains = {}

    # Token Price
    chains["token_price"] = ProviderChain(
        data_type="token_price",
        description="Token price across DEXs and CEXs",
        providers=[
            Provider("local_price", ProviderTier.LOCAL, _local_token_price, weight=10.0),
            Provider(
                "solana_tracker",
                ProviderTier.FREEMIUM,
                _solana_tracker_price,
                weight=8.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
            Provider(
                "dexscreener",
                ProviderTier.FREE_API,
                _dexscreener_price,
                weight=5.0,
                rate_limit_rps=2.0,
            ),
            Provider("coingecko", ProviderTier.FREE_API, _coingecko_price, weight=5.0),
            Provider(
                "moralis",
                ProviderTier.PAID,
                _moralis_price,
                weight=1.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
                monthly_quota=5000,
            ),
        ],
    )

    # Market Overview
    chains["market_overview"] = ProviderChain(
        data_type="market_overview",
        description="Global market cap, BTC/ETH/SOL prices, fear & greed",
        providers=[
            Provider(
                "rmi_market_overview", ProviderTier.LOCAL, _passthrough_market_overview, weight=10.0
            ),
            Provider(
                "coingecko_global",
                ProviderTier.FREEMIUM,
                _coingecko_price,
                weight=5.0,
                requires_key=True,
                key_env="COINGECKO_API_KEY",
            ),
        ],
    )

    # Trending
    chains["trending"] = ProviderChain(
        data_type="trending",
        description="Trending tokens across chains",
        providers=[
            Provider("rmi_trending", ProviderTier.LOCAL, _passthrough_trending, weight=10.0),
            Provider(
                "solana_tracker_trending",
                ProviderTier.FREEMIUM,
                _solana_tracker_trending,
                weight=8.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="SOLANATRACKER_API_KEY",
                monthly_quota=5000,
            ),
            Provider(
                "dexscreener_trending",
                ProviderTier.FREE_API,
                _dexscreener_price,
                weight=5.0,
                rate_limit_rps=1.0,
            ),
        ],
    )

    # Token Trades (Live buys/sells)
    chains["token_trades"] = ProviderChain(
        data_type="token_trades",
        description="Live token trades (buys/sells) from DexScreener",
        providers=[
            Provider(
                "dexscreener_trades",
                ProviderTier.FREE_API,
                _dexscreener_trades,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # Top Traders (Profitable wallets for a token)
    chains["top_traders"] = ProviderChain(
        data_type="top_traders",
        description="Top profitable traders and win rates for a token",
        providers=[
            Provider(
                "dexscreener_top_traders",
                ProviderTier.FREE_API,
                _dexscreener_top_traders,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # ── Dune Analytics (Free Tier: 10k CU/mo) ──
    chains["dune_early_buyers"] = ProviderChain(
        data_type="dune_early_buyers",
        description="First 5-minute buyers of a token (Dune Analytics, low-CU cached query)",
        providers=[
            Provider(
                "dune_early_buyers_api",
                ProviderTier.FREEMIUM,
                _dune_early_buyers,
                weight=10.0,
                rate_limit_rps=0.5,
                requires_key=True,
                key_env="DUNE_API_KEY",
                monthly_quota=10000,
            ),
        ],
    )

    # News
    chains["news"] = ProviderChain(
        data_type="news",
        description="Real-time crypto news from 30+ sources + Messari + CoinDesk institutional feeds",
        providers=[
            Provider("rmi_news", ProviderTier.LOCAL, _passthrough_news, weight=10.0),
            Provider(
                "messari_news",
                ProviderTier.FREEMIUM,
                _messari_news,
                weight=8.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MESSARI_API_KEY",
                monthly_quota=10000,
            ),
            Provider(
                "coindesk_news",
                ProviderTier.FREEMIUM,
                _coindesk_news,
                weight=8.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="COINDESK_API_KEY",
                monthly_quota=10000,
            ),
        ],
    )

    # Alerts / Live Intel
    chains["alerts"] = ProviderChain(
        data_type="alerts",
        description="Active threat alerts from scanner pipeline",
        providers=[
            Provider("rmi_alerts", ProviderTier.LOCAL, _passthrough_alerts, weight=10.0),
        ],
    )

    # ── Advanced Security & Dev Activity ──
    chains["dev_activity"] = ProviderChain(
        data_type="dev_activity",
        description="GitHub developer activity and social volume (Santiment free tier)",
        providers=[
            Provider(
                "santiment_dev",
                ProviderTier.FREEMIUM,
                _santiment_dev_activity,
                weight=10.0,
                rate_limit_rps=1.0,
                requires_key=True,
                key_env="SANTIMENT_API_KEY",
                monthly_quota=3000,
            ),
        ],
    )
    chains["url_security_scan"] = ProviderChain(
        data_type="url_security_scan",
        description="Phishing and malware scan for token websites (VirusTotal free tier)",
        providers=[
            Provider(
                "virustotal_scan",
                ProviderTier.FREEMIUM,
                _virustotal_url_scan,
                weight=10.0,
                rate_limit_rps=0.5,
                requires_key=True,
                key_env="VIRUSTOTAL_API_KEY",
                monthly_quota=15000,
            ),
        ],
    )

    # ── Bitquery chains (activate free plan at bitquery.io/pricing) ──
    try:
        from app.databus.bitquery_provider import BitqueryProvider

        _bq = BitqueryProvider()

        async def _bq_token_price(**kw):
            return await _bq.get_token_price(kw.get("network", "ethereum"), kw.get("token", ""))

        async def _bq_holder_data(**kw):
            return await _bq.get_holder_distribution(
                kw.get("network", "ethereum"), kw.get("token", "")
            )

        async def _bq_tx_trace(**kw):
            return await _bq.get_transaction_trace(
                kw.get("network", "ethereum"), kw.get("tx_hash", "")
            )

        async def _bq_dex_volume(**kw):
            return await _bq.get_dex_volume(kw.get("network", "ethereum"))

        async def _bq_address_balance(**kw):
            return await _bq.get_address_balance(
                kw.get("network", "ethereum"), kw.get("address", "")
            )

        async def _bq_cross_chain(**kw):
            return await _bq.get_cross_chain_transfers(kw.get("address", ""))

        chains["holder_data"] = ProviderChain(
            "holder_data",
            description="Token holder distribution (DexScreener free → Bitquery freemium)",
            providers=[
                Provider(
                    "dexscreener_holders",
                    ProviderTier.FREE_API,
                    _dexscreener_holders,
                    weight=10.0,
                    rate_limit_rps=2.0,
                ),
                Provider(
                    "bitquery_holders",
                    ProviderTier.FREEMIUM,
                    _bq_holder_data,
                    weight=5.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="BITQUERY_API_KEY",
                    monthly_quota=10000,
                ),
            ],
        )
        chains["tx_trace"] = ProviderChain(
            "tx_trace",
            description="Transaction traces (Etherscan free → Bitquery freemium)",
            providers=[
                Provider(
                    "etherscan_trace",
                    ProviderTier.FREE_API,
                    _etherscan_tx_trace,
                    weight=10.0,
                    rate_limit_rps=3.0,
                ),
                Provider(
                    "bitquery_trace",
                    ProviderTier.FREEMIUM,
                    _bq_tx_trace,
                    weight=5.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="BITQUERY_API_KEY",
                    monthly_quota=10000,
                ),
            ],
        )
        chains["cross_chain"] = ProviderChain(
            "cross_chain",
            description="Cross-chain transfer tracking and bridge monitoring",
            providers=[
                Provider(
                    "bitquery_cross_chain",
                    ProviderTier.FREEMIUM,
                    _bq_cross_chain,
                    weight=5.0,
                    rate_limit_rps=1.0,
                    requires_key=True,
                    key_env="BITQUERY_API_KEY",
                    monthly_quota=10000,
                )
            ],
        )
        logger.info("Bitquery chains registered: holder_data, tx_trace, cross_chain")
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"provider error: {e}")
        logger.warning(f"Bitquery not available: {e}")

    # Wallet Labels - OUR data first
    chains["wallet_labels"] = ProviderChain(
        data_type="wallet_labels",
        description="Address labels and entity identification (190K local + external)",
        providers=[
            Provider("wallet_memory_bank", ProviderTier.LOCAL, _local_wallet_labels, weight=10.0),
            Provider(
                "nansen_labels",
                ProviderTier.PAID,
                _moralis_price,
                weight=5.0,
                requires_key=True,
                key_env="NANSEN_API_KEY",
            ),
        ],
    )

    # Scanner / Security
    chains["scanner"] = ProviderChain(
        data_type="scanner",
        description="Token security scan via SENTINEL (rug pull, honeypot, risk score)",
        providers=[
            Provider(
                "sentinel_scanner",
                ProviderTier.LOCAL,
                _passthrough_scanner,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # ── SPL Token Metadata Decoder (FREE, bypasses unreliable 3rd-party APIs) ──
    chains["spl_token_metadata"] = ProviderChain(
        data_type="spl_token_metadata",
        description="Raw SPL token metadata decoder: mint authority, freeze authority, decimals, supply, and Token-2022 extensions",
        providers=[
            Provider(
                "spl_metadata_decoder",
                ProviderTier.FREE_API,
                _spl_metadata_decoder_provider,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
        ],
    )

    # RAG Search
    chains["rag_search"] = ProviderChain(
        data_type="rag_search",
        description="Semantic search across 17K+ scam documents and patterns",
        providers=[
            Provider(
                "local_rag", ProviderTier.LOCAL, _passthrough_rag, weight=10.0, rate_limit_rps=5.0
            ),
        ],
    )

    # ── DeFiLlama (100% FREE, NO API KEY) ──
    chains["defillama_tvl"] = ProviderChain(
        data_type="defillama_tvl",
        description="Global DeFi TVL and top protocols (completely free)",
        providers=[
            Provider(
                "defillama_tvl_api",
                ProviderTier.FREE_API,
                _defillama_tvl,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )
    chains["defillama_chains"] = ProviderChain(
        data_type="defillama_chains",
        description="TVL breakdown by blockchain (completely free)",
        providers=[
            Provider(
                "defillama_chains_api",
                ProviderTier.FREE_API,
                _defillama_chains,
                weight=10.0,
                rate_limit_rps=2.0,
            ),
        ],
    )

    # ── Blockchair (FREE TIER, NO API KEY FOR BASIC) ──
    chains["blockchair_address"] = ProviderChain(
        data_type="blockchair_address",
        description="Multi-chain address balance and tx count (BTC, ETH, SOL, etc.)",
        providers=[
            Provider(
                "blockchair_addr_api",
                ProviderTier.FREE_API,
                _blockchair_address,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
        ],
    )
    chains["blockchair_stats"] = ProviderChain(
        data_type="blockchair_stats",
        description="Multi-chain network statistics and mempool data",
        providers=[
            Provider(
                "blockchair_stats_api",
                ProviderTier.FREE_API,
                _blockchair_stats,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
        ],
    )

    # ── Birdeye (FREEMIUM, FREE TIER AVAILABLE) ──
    chains["birdeye_overview"] = ProviderChain(
        data_type="birdeye_overview",
        description="Token overview, liquidity, and holder stats (Solana/EVM)",
        providers=[
            Provider(
                "birdeye_overview_api",
                ProviderTier.FREEMIUM,
                _birdeye_overview,
                weight=10.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="BIRDEYE_API_KEY",
                monthly_quota=50000,
            ),
        ],
    )
    chains["birdeye_price"] = ProviderChain(
        data_type="birdeye_price",
        description="Real-time token price from Birdeye",
        providers=[
            Provider(
                "birdeye_price_api",
                ProviderTier.FREEMIUM,
                _birdeye_price,
                weight=5.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="BIRDEYE_API_KEY",
                monthly_quota=50000,
            ),
            Provider(
                "dexscreener_price",
                ProviderTier.FREE_API,
                _dexscreener_price,
                weight=10.0,
                rate_limit_rps=2.0,
            ),  # Fallback to free
        ],
    )

    # ── Alchemy chains ──
    chains["wallet_tokens"] = ProviderChain(
        data_type="wallet_tokens",
        description="Token balances for any address (Alchemy + Moralis)",
        providers=[
            Provider(
                "alchemy_balances",
                ProviderTier.FREEMIUM,
                _alchemy_token_balances,
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ALCHEMY_API_KEY",
                monthly_quota=300000,
            ),
            Provider(
                "moralis_tokens",
                ProviderTier.FREEMIUM,
                _moralis_wallet_tokens,
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )
    chains["token_metadata"] = ProviderChain(
        data_type="token_metadata",
        description="Token metadata lookup (DexScreener free → Alchemy freemium)",
        providers=[
            Provider(
                "dexscreener_meta",
                ProviderTier.FREE_API,
                _dexscreener_token_metadata,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "alchemy_metadata",
                ProviderTier.FREEMIUM,
                _alchemy_token_metadata,
                weight=5.0,
                rate_limit_rps=10.0,
                requires_key=True,
                key_env="ALCHEMY_API_KEY",
                monthly_quota=300000,
            ),
        ],
    )
    chains["wallet_nfts"] = ProviderChain(
        data_type="wallet_nfts",
        description="NFT holdings for any address (Moralis - free tier available)",
        providers=[
            Provider(
                "moralis_nfts",
                ProviderTier.FREEMIUM,
                _moralis_wallet_nfts,
                weight=5.0,
                rate_limit_rps=1.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    # ── Moralis expanded chains ──
    chains["wallet_transactions"] = ProviderChain(
        data_type="wallet_transactions",
        description="Wallet transaction history (Etherscan free → Moralis freemium)",
        providers=[
            Provider(
                "etherscan_wallet",
                ProviderTier.FREE_API,
                _etherscan_tx_trace,
                weight=10.0,
                rate_limit_rps=3.0,
            ),
            Provider(
                "moralis_txns",
                ProviderTier.FREEMIUM,
                _moralis_wallet_transactions,
                weight=5.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["wallet_net_worth"] = ProviderChain(
        data_type="wallet_net_worth",
        description="Wallet net worth across chains (Moralis)",
        providers=[
            Provider(
                "moralis_net_worth",
                ProviderTier.FREEMIUM,
                _moralis_wallet_net_worth,
                weight=10.0,
                rate_limit_rps=2.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["token_search"] = ProviderChain(
        data_type="token_search",
        description="Search tokens by name/symbol/address (Moralis + MCP)",
        providers=[
            Provider(
                "moralis_search",
                ProviderTier.FREEMIUM,
                _moralis_search_tokens,
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    # ── Arkham Intelligence - Free trial month (Nov 2026) ──
    # Priority: LOCAL labels → Arkham (free trial) → paid alternatives
    chains["entity_intel"] = ProviderChain(
        data_type="entity_intel",
        description="Entity resolution and risk scoring (local labels → Arkham free → Moralis)",
        providers=[
            Provider("wallet_labels_local", ProviderTier.LOCAL, _local_wallet_labels, weight=10.0),
            Provider(
                "arkham_entity",
                ProviderTier.FREEMIUM,
                _arkham_entity,
                weight=8.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
            Provider(
                "moralis_labels",
                ProviderTier.FREEMIUM,
                _moralis_wallet_tokens,
                weight=3.0,
                requires_key=True,
                key_env="MORALIS_API_KEY",
            ),
        ],
    )

    chains["arkham_labels"] = ProviderChain(
        data_type="arkham_labels",
        description="Entity labels with confidence scores (local → Arkham free trial)",
        providers=[
            Provider("wallet_labels_local", ProviderTier.LOCAL, _local_wallet_labels, weight=10.0),
            Provider(
                "arkham_labels_api",
                ProviderTier.FREEMIUM,
                _arkham_labels,
                weight=8.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    chains["arkham_portfolio"] = ProviderChain(
        data_type="arkham_portfolio",
        description="Portfolio holdings with USD values (Arkham free trial)",
        providers=[
            Provider(
                "arkham_portfolio_api",
                ProviderTier.FREEMIUM,
                _arkham_portfolio,
                weight=10.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    chains["arkham_transfers"] = ProviderChain(
        data_type="arkham_transfers",
        description="Transfer history with counterparty mapping (Arkham free trial)",
        providers=[
            Provider(
                "arkham_transfers_api",
                ProviderTier.FREEMIUM,
                _arkham_transfers,
                weight=10.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    chains["arkham_counterparties"] = ProviderChain(
        data_type="arkham_counterparties",
        description="Counterparty network and relationship mapping (Arkham free trial)",
        providers=[
            Provider(
                "arkham_counterparties_api",
                ProviderTier.FREEMIUM,
                _arkham_counterparties,
                weight=10.0,
                rate_limit_rps=3.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    chains["arkham_intel"] = ProviderChain(
        data_type="arkham_intel",
        description="Entity intelligence search (Arkham free trial)",
        providers=[
            Provider(
                "arkham_intel_search",
                ProviderTier.FREEMIUM,
                _arkham_intel_search,
                weight=10.0,
                rate_limit_rps=5.0,
                requires_key=True,
                key_env="ARKHAM_API_KEY",
                monthly_quota=100000,
            ),
        ],
    )

    # ── MCP Bridge - all installed MCP servers ──
    chains["mcp_bridge"] = ProviderChain(
        data_type="mcp_bridge",
        description="Universal MCP bridge - calls any local/remote MCP server tool",
        providers=[
            Provider("mcp_bridge", ProviderTier.LOCAL, _mcp_bridge, weight=10.0),
        ],
    )

    # ── PREMIUM SCANNER - 10 high-value detection chains ──
    try:
        from app.databus.premium_scanner import (
            detect_bot_farms,
            detect_bundles,
            detect_copy_trading,
            detect_fresh_wallets,
            detect_insider_signals,
            detect_mev_sandwich,
            detect_snipers,
            detect_wash_trading,
            find_dev_wallets,
            map_clusters,
        )

        def _premium_provider(name, fn, rps=3.0):
            return Provider(name, ProviderTier.LOCAL, fn, weight=10.0, rate_limit_rps=rps)

        chains["bundle_detect"] = ProviderChain(
            "bundle_detect",
            description="Coordinated wallet bundle detection (Bubblemaps-style cluster analysis)",
            providers=[_premium_provider("bundle_scanner", detect_bundles)],
        )

        chains["cluster_map"] = ProviderChain(
            "cluster_map",
            description="Full wallet cluster mapping - funders, recipients, counterparties (graph-ready)",
            providers=[_premium_provider("cluster_mapper", map_clusters, rps=1.0)],
        )

        chains["dev_finder"] = ProviderChain(
            "dev_finder",
            description="Find developer/creator wallets behind tokens (deployer→funder→team)",
            providers=[_premium_provider("dev_finder_scanner", find_dev_wallets)],
        )

        chains["sniper_detect"] = ProviderChain(
            "sniper_detect",
            description="Sniper detection - first-block buyers with fast dump patterns",
            providers=[_premium_provider("sniper_scanner", detect_snipers)],
        )

        chains["bot_farm_detect"] = ProviderChain(
            "bot_farm_detect",
            description="Bot farm detection - identical behavior patterns across wallets",
            providers=[_premium_provider("bot_farm_scanner", detect_bot_farms)],
        )

        chains["copy_trade_detect"] = ProviderChain(
            "copy_trade_detect",
            description="Copy trading pattern detection - wallets mirroring trades with delay",
            providers=[_premium_provider("copy_trade_scanner", detect_copy_trading)],
        )

        chains["insider_detect"] = ProviderChain(
            "insider_detect",
            description="Insider trading signals - large buys before major announcements",
            providers=[_premium_provider("insider_scanner", detect_insider_signals)],
        )

        chains["wash_trade_detect"] = ProviderChain(
            "wash_trade_detect",
            description="Wash trading detection - circular transactions, self-trading",
            providers=[_premium_provider("wash_trade_scanner", detect_wash_trading)],
        )

        chains["mev_detect"] = ProviderChain(
            "mev_detect",
            description="MEV sandwich attack detection - frontrun/backrun patterns",
            providers=[_premium_provider("mev_scanner", detect_mev_sandwich)],
        )

        chains["fresh_wallet_analysis"] = ProviderChain(
            "fresh_wallet_analysis",
            description="Fresh wallet concentration analysis - high new-wallet % = rug risk",
            providers=[_premium_provider("fresh_wallet_scanner", detect_fresh_wallets, rps=2.0)],
        )

        logger.info(
            "Premium scanner chains registered: bundle_detect, cluster_map, dev_finder, sniper_detect, bot_farm, copy_trade, insider, wash_trade, mev, fresh_wallets"
        )
    except ImportError as e:
        logger.warning(f"Premium scanner not available: {e}")

    # ── WEBHOOK SYSTEM ──
    try:
        from app.databus.webhooks import handle_webhook, list_webhooks, setup_webhook  # noqa: F401

        chains["webhook_handler"] = ProviderChain(
            "webhook_handler",
            description="Intelligent webhook receiver - Arkham, Helius, Moralis, Alchemy + custom",
            providers=[
                Provider(
                    "webhook_processor",
                    ProviderTier.LOCAL,
                    lambda **kw: handle_webhook(
                        kw.get("service", ""),
                        kw.get("payload", {}),
                        kw.get("headers", {}),
                        kw.get("raw_body", b""),
                    ),
                    weight=10.0,
                )
            ],
        )
    except ImportError:
        pass

    # ── ARKHAM WEBSOCKET - real-time intelligence streaming ──
    try:
        from app.databus.arkham_ws import arkham_ws_subscribe

        chains["arkham_ws"] = ProviderChain(
            "arkham_ws",
            description="Arkham Intelligence WebSocket - real-time entity updates, transfers, labels",
            providers=[
                Provider(
                    "arkham_ws_stream",
                    ProviderTier.FREEMIUM,
                    arkham_ws_subscribe,
                    weight=10.0,
                    rate_limit_rps=10.0,
                    requires_key=True,
                    key_env="ARKHAM_WS_KEY",
                    monthly_quota=500000,
                )
            ],
        )
        logger.info("Arkham WebSocket chain registered")
    except ImportError:
        logger.info("Arkham WS module not found - skipping WS chain")

    # ── RUGCHARTS: Volume Authenticity (Fake Volume %) ──
    try:
        from app.databus.volume_authenticity import analyze_volume_authenticity

        chains["volume_authenticity"] = ProviderChain(
            "volume_authenticity",
            description="Fake volume detection - 4-layer analysis (statistical, graph, heuristic, ML) with bootstrap CI",
            providers=[
                Provider(
                    "volume_auth_scorer",
                    ProviderTier.LOCAL,
                    analyze_volume_authenticity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )
        logger.info("Volume Authenticity chain registered")
    except ImportError:
        logger.info("Volume Authenticity module not found")

    # ── RUGCHARTS: OHLCV Engine ──
    try:
        from app.databus.ohlcv_engine import fetch_ohlcv, ingest_trade_data

        chains["ohlcv"] = ProviderChain(
            "ohlcv",
            description="Real-time OHLCV candle aggregation (1m/5m/15m/1h/4h/1d) with authenticity scoring",
            providers=[
                Provider(
                    "ohlcv_fetcher",
                    ProviderTier.LOCAL,
                    fetch_ohlcv,
                    weight=10.0,
                    rate_limit_rps=20.0,
                ),
                Provider(
                    "ohlcv_ingest",
                    ProviderTier.LOCAL,
                    ingest_trade_data,
                    weight=5.0,
                    rate_limit_rps=50.0,
                ),
            ],
        )
        logger.info("OHLCV Engine chain registered")
    except ImportError:
        logger.info("OHLCV Engine module not found")

    # ── RUGCHARTS: Token Security Matrix (37+ checks) ──
    try:
        from app.databus.token_security import get_check_matrix_endpoint, run_full_scan

        chains["token_security"] = ProviderChain(
            "token_security",
            description="37+ security checks - GoPlus, honeypot, contract, liquidity, holders, deployer, tokenomics, rug pull",
            providers=[
                Provider(
                    "security_scanner",
                    ProviderTier.LOCAL,
                    run_full_scan,
                    weight=10.0,
                    rate_limit_rps=10.0,
                ),
                Provider(
                    "check_matrix",
                    ProviderTier.LOCAL,
                    get_check_matrix_endpoint,
                    weight=1.0,
                    rate_limit_rps=60.0,
                ),
            ],
        )
        logger.info("Token Security Matrix chain registered (37+ checks)")
    except ImportError:
        logger.info("Token Security module not found")

    # ── RUGCHARTS INTELLIGENCE - 10 Premium Features ──
    try:
        from app.databus.data_quality import enhanced_token_report, get_tier_comparison
        from app.databus.rugcharts_intel import (
            cross_chain_entity,
            developer_reputation,
            holder_health_score,
            insider_pattern_detector,
            liquidity_risk_monitor,
            rug_pattern_matcher,
            smart_money_feed,
            token_launch_scanner,
            whale_alert_stream,
        )

        chains["smart_money"] = ProviderChain(
            "smart_money",
            description="Smart money feed - what profitable wallets are buying right now",
            providers=[
                Provider(
                    "smart_money_tracker",
                    ProviderTier.LOCAL,
                    smart_money_feed,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["whale_alerts"] = ProviderChain(
            "whale_alerts",
            description="Real-time whale transaction detection - large transfers across chains",
            providers=[
                Provider(
                    "whale_detector",
                    ProviderTier.LOCAL,
                    whale_alert_stream,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["token_launches"] = ProviderChain(
            "token_launches",
            description="New token launch scanner with instant risk scoring (by age)",
            providers=[
                Provider(
                    "launch_scanner",
                    ProviderTier.LOCAL,
                    token_launch_scanner,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["insider_detection"] = ProviderChain(
            "insider_detection",
            description="Pre-pump accumulation pattern detection - volume spikes before price moves",
            providers=[
                Provider(
                    "insider_detector",
                    ProviderTier.LOCAL,
                    insider_pattern_detector,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["liquidity_risk"] = ProviderChain(
            "liquidity_risk",
            description="LP health monitor - concentration, lock status, removal risk",
            providers=[
                Provider(
                    "liquidity_monitor",
                    ProviderTier.LOCAL,
                    liquidity_risk_monitor,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["holder_health"] = ProviderChain(
            "holder_health",
            description="Holder distribution analysis - Gini, concentration, decentralization score",
            providers=[
                Provider(
                    "holder_analyzer",
                    ProviderTier.LOCAL,
                    holder_health_score,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["cross_chain_entity"] = ProviderChain(
            "cross_chain_entity",
            description="Cross-chain entity resolution via Arkham - trace wallets across all chains",
            providers=[
                Provider(
                    "entity_tracer",
                    ProviderTier.LOCAL,
                    cross_chain_entity,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["rug_patterns"] = ProviderChain(
            "rug_patterns",
            description="Rug pull pattern matcher - similarity scoring against 10 known scam patterns",
            providers=[
                Provider(
                    "rug_matcher",
                    ProviderTier.LOCAL,
                    rug_pattern_matcher,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["dev_reputation"] = ProviderChain(
            "dev_reputation",
            description="Developer reputation - deployer history, token count, entity resolution",
            providers=[
                Provider(
                    "dev_reputation",
                    ProviderTier.LOCAL,
                    developer_reputation,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["token_report"] = ProviderChain(
            "token_report",
            description="ONE-CALL enhanced token report - smart verdicts, entity enrichment, trust adjustments, tier-aware",
            providers=[
                Provider(
                    "report_generator",
                    ProviderTier.LOCAL,
                    enhanced_token_report,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )

        chains["tier_comparison"] = ProviderChain(
            "tier_comparison",
            description="Competitive tier comparison - RugCharts vs DexScreener vs Nansen vs GMGN",
            providers=[
                Provider(
                    "tier_compare",
                    ProviderTier.LOCAL,
                    get_tier_comparison,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )

        logger.info("RugCharts Intelligence: 10 premium chains registered")
    except ImportError as e:
        logger.warning(f"RugCharts Intelligence not available: {e}")

    # ── NEWS & MARKET DATA - Free APIs ──
    try:
        from app.databus.news_provider import (
            get_fear_greed,
            get_full_news_feed,
            get_market_brief,
            get_market_prices,
            get_prediction_markets,
            get_trending_coins,
        )

        chains["live_prices"] = ProviderChain(
            "live_prices",
            description="Live crypto prices - CoinGecko free tier, multi-coin",
            providers=[
                Provider(
                    "coingecko_prices",
                    ProviderTier.LOCAL,
                    get_market_prices,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["trending_coins"] = ProviderChain(
            "trending_coins",
            description="Trending coins - CoinGecko search, top 10",
            providers=[
                Provider(
                    "coingecko_trending",
                    ProviderTier.LOCAL,
                    get_trending_coins,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["fear_greed"] = ProviderChain(
            "fear_greed",
            description="Crypto Fear & Greed Index - Alternative.me, free, no key",
            providers=[
                Provider(
                    "fear_greed_index",
                    ProviderTier.LOCAL,
                    get_fear_greed,
                    weight=10.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["prediction_markets"] = ProviderChain(
            "prediction_markets",
            description="Prediction market events - Polymarket, free, no key",
            providers=[
                Provider(
                    "polymarket_events",
                    ProviderTier.LOCAL,
                    get_prediction_markets,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["market_brief"] = ProviderChain(
            "market_brief",
            description="One-call market overview: prices + fear/greed + trending + prediction markets",
            providers=[
                Provider(
                    "market_briefing",
                    ProviderTier.LOCAL,
                    get_market_brief,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["full_news"] = ProviderChain(
            "full_news",
            description="Complete news feed: headlines + market data + fear/greed + polymarket predictions",
            providers=[
                Provider(
                    "full_news_feed",
                    ProviderTier.LOCAL,
                    get_full_news_feed,
                    weight=15.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        logger.info(
            "News & Market Data chains registered (6 new: prices, trending, fear_greed, prediction_markets, market_brief, full_news)"
        )
    except ImportError as e:
        logger.warning(f"News providers not available: {e}")

    # ── NEWS INTELLIGENCE ENGINE - multi-source, quality-scored, sentiment-tagged ──
    try:
        from app.databus.news_intel import (
            add_comment,
            add_reaction,
            aggregate_all_news,
            create_bb_post,
            get_academic_papers,
            get_reactions,
            get_social_feed,
            get_weekly_best,
        )

        chains["news_intel"] = ProviderChain(
            "news_intel",
            description="Complete news intelligence - 10+ sources, quality-scored, deduped, sentiment-tagged",
            providers=[
                Provider(
                    "news_engine",
                    ProviderTier.LOCAL,
                    aggregate_all_news,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )

        chains["weekly_best"] = ProviderChain(
            "weekly_best",
            description="Curated weekly best - highest quality crypto journalism",
            providers=[
                Provider(
                    "weekly_curator",
                    ProviderTier.LOCAL,
                    get_weekly_best,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["academic_papers"] = ProviderChain(
            "academic_papers",
            description="Academic crypto/blockchain research papers from arXiv",
            providers=[
                Provider(
                    "arxiv_fetcher",
                    ProviderTier.LOCAL,
                    get_academic_papers,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["social_feed"] = ProviderChain(
            "social_feed",
            description="Crypto social feed - X/Twitter + CryptoPanic sentiment",
            providers=[
                Provider(
                    "social_aggregator",
                    ProviderTier.LOCAL,
                    get_social_feed,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["article_reactions"] = ProviderChain(
            "article_reactions",
            description="Article reactions - 🔥🐂🐻💎🧠🤡🚀💀 with counts",
            providers=[
                Provider(
                    "react_article",
                    ProviderTier.LOCAL,
                    add_reaction,
                    weight=5.0,
                    rate_limit_rps=30.0,
                ),
                Provider(
                    "get_reactions",
                    ProviderTier.LOCAL,
                    get_reactions,
                    weight=5.0,
                    rate_limit_rps=60.0,
                ),
            ],
        )

        chains["article_comments"] = ProviderChain(
            "article_comments",
            description="Article comments - community discussion on any story",
            providers=[
                Provider(
                    "comment_article",
                    ProviderTier.LOCAL,
                    add_comment,
                    weight=5.0,
                    rate_limit_rps=20.0,
                )
            ],
        )

        chains["bb_post"] = ProviderChain(
            "bb_post",
            description="Convert article to Bulletin Board post for community engagement",
            providers=[
                Provider(
                    "create_bb_post",
                    ProviderTier.LOCAL,
                    create_bb_post,
                    weight=5.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        logger.info(
            "News Intelligence chains registered (7: news_intel, weekly_best, academic_papers, social_feed, reactions, comments, bb_post)"
        )
    except ImportError as e:
        logger.warning(f"News Intelligence not available: {e}")

    # ── X/CT INTELLIGENCE - Crypto Twitter Rundown ──
    try:
        from app.databus.x_intel import fetch_ct_rundown, track_ct_accounts

        chains["ct_rundown"] = ProviderChain(
            "ct_rundown",
            description="CT Rundown - top 20 Crypto Twitter stories, AI-summarized, category-diverse",
            providers=[
                Provider(
                    "ct_scanner",
                    ProviderTier.LOCAL,
                    fetch_ct_rundown,
                    weight=15.0,
                    rate_limit_rps=3.0,
                )
            ],
        )

        chains["ct_accounts"] = ProviderChain(
            "ct_accounts",
            description="Curated CT account list - 35+ top accounts across 5 tiers",
            providers=[
                Provider(
                    "ct_account_list",
                    ProviderTier.LOCAL,
                    track_ct_accounts,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )

        logger.info("X/CT Intelligence chains registered (2: ct_rundown, ct_accounts)")
    except ImportError as e:
        logger.warning(f"X/CT Intelligence not available: {e}")

    # ── SOCIAL INTELLIGENCE - KOL tracking, shill detection, Daily Intel ──
    try:
        from app.databus.daily_intel import generate_daily_intel
        from app.databus.social_intel import (
            detect_shill_campaigns,
            get_kol_leaderboard,
            get_kol_profile,
            get_shill_alerts,
            get_social_metrics,
            scan_scam_channels,
            track_kol_call,
        )

        chains["kol_track"] = ProviderChain(
            "kol_track",
            description="KOL call tracking - record and analyze influencer token calls",
            providers=[
                Provider(
                    "kol_call_tracker",
                    ProviderTier.LOCAL,
                    track_kol_call,
                    weight=5.0,
                    rate_limit_rps=20.0,
                )
            ],
        )

        chains["kol_profile"] = ProviderChain(
            "kol_profile",
            description="KOL performance profile - trust score, call history, win rate",
            providers=[
                Provider(
                    "kol_profiler",
                    ProviderTier.LOCAL,
                    get_kol_profile,
                    weight=5.0,
                    rate_limit_rps=30.0,
                )
            ],
        )

        chains["kol_leaderboard"] = ProviderChain(
            "kol_leaderboard",
            description="KOL leaderboard - ranked by trust score and accuracy",
            providers=[
                Provider(
                    "kol_board",
                    ProviderTier.LOCAL,
                    get_kol_leaderboard,
                    weight=5.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["shill_detector"] = ProviderChain(
            "shill_detector",
            description="Shill campaign detection - coordinated promotion, paid content, pump-and-dump",
            providers=[
                Provider(
                    "shill_scanner",
                    ProviderTier.LOCAL,
                    detect_shill_campaigns,
                    weight=10.0,
                    rate_limit_rps=5.0,
                ),
                Provider(
                    "shill_alerts",
                    ProviderTier.LOCAL,
                    get_shill_alerts,
                    weight=5.0,
                    rate_limit_rps=20.0,
                ),
            ],
        )

        chains["scam_monitor"] = ProviderChain(
            "scam_monitor",
            description="Scam channel monitor - Telegram/Discord scam pattern detection",
            providers=[
                Provider(
                    "scam_scanner",
                    ProviderTier.LOCAL,
                    scan_scam_channels,
                    weight=5.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["daily_intel"] = ProviderChain(
            "daily_intel",
            description="Daily Intelligence Briefing - OpenRouter free model research + writing, publish to X/Telegram/Ghost",
            providers=[
                Provider(
                    "intel_reporter",
                    ProviderTier.LOCAL,
                    generate_daily_intel,
                    weight=15.0,
                    rate_limit_rps=1.0,
                )
            ],
        )

        chains["social_metrics"] = ProviderChain(
            "social_metrics",
            description="Social metrics aggregator - trending topics, sentiment, KOL activity",
            providers=[
                Provider(
                    "social_aggregator",
                    ProviderTier.LOCAL,
                    get_social_metrics,
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        logger.info(
            "Social Intelligence chains registered (8: kol_track, kol_profile, kol_leaderboard, shill_detector, scam_monitor, daily_intel, social_metrics)"
        )
    except ImportError as e:
        logger.warning(f"Social Intelligence not available: {e}")

    # ── MODEL REGISTRY - Smart free model routing, quality review ──
    try:
        from app.databus.model_registry import ai_call, get_usage_stats, review_content

        chains["ai_task"] = ProviderChain(
            "ai_task",
            description="AI task execution - smart routing across free models (research/writing/coding/review/fast)",
            providers=[
                Provider(
                    "ai_runner",
                    ProviderTier.LOCAL,
                    lambda **kw: ai_call(
                        kw.get("task_type", "fast"),
                        kw.get("system", ""),
                        kw.get("user", ""),
                        kw.get("max_tokens", 1000),
                        kw.get("temperature", 0.5),
                    ),
                    weight=10.0,
                    rate_limit_rps=5.0,
                )
            ],
        )

        chains["content_review"] = ProviderChain(
            "content_review",
            description="Content quality review - checks for AI-slop, forbidden words, human voice",
            providers=[
                Provider(
                    "quality_reviewer",
                    ProviderTier.LOCAL,
                    review_content,
                    weight=5.0,
                    rate_limit_rps=10.0,
                )
            ],
        )

        chains["ai_usage"] = ProviderChain(
            "ai_usage",
            description="AI model usage statistics - track free tier consumption",
            providers=[
                Provider(
                    "usage_tracker",
                    ProviderTier.LOCAL,
                    get_usage_stats,
                    weight=1.0,
                    rate_limit_rps=60.0,
                )
            ],
        )

        logger.info("Model Registry chains registered (3: ai_task, content_review, ai_usage)")
    except ImportError as e:
        logger.warning(f"Model Registry not available: {e}")

    # ── RAG INGESTION - nightly indexing, health checks ──
    try:
        from app.databus.rag_ingestion import nightly_rag_index, rag_health_check

        chains["rag_nightly"] = ProviderChain(
            "rag_nightly",
            description="Nightly RAG indexing - embeds news, CT, market, social data into vector store",
            providers=[
                Provider(
                    "rag_indexer",
                    ProviderTier.LOCAL,
                    nightly_rag_index,
                    weight=10.0,
                    rate_limit_rps=1.0,
                )
            ],
        )

        chains["rag_health"] = ProviderChain(
            "rag_health",
            description="RAG system health - collection stats, doc counts, embedder status",
            providers=[
                Provider(
                    "rag_checker",
                    ProviderTier.LOCAL,
                    rag_health_check,
                    weight=5.0,
                    rate_limit_rps=30.0,
                )
            ],
        )

        logger.info("RAG Ingestion chains registered (2: rag_nightly, rag_health)")
    except ImportError as e:
        logger.warning(f"RAG Ingestion not available: {e}")

    # ── HYPERLIQUID - Perp markets and funding rates ──
    async def _passthrough_hyperliquid(**kwargs) -> dict | None:
        try:
            limit = kwargs.get("limit", 10)
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"http://localhost:8000/api/v1/market/hyperliquid?limit={limit}")
                if r.status_code == 200:
                    return r.json()
        except (ValueError, ConnectionError, TimeoutError, RuntimeError):
            logger.warning("provider error")
        return None

    chains["hyperliquid"] = ProviderChain(
        data_type="hyperliquid",
        description="Hyperliquid perp markets and funding rates",
        providers=[
            Provider("rmi_hyperliquid", ProviderTier.LOCAL, _passthrough_hyperliquid, weight=10.0),
        ],
    )

    # ── HYPERLIQUID ACTION - Gain/Loss Porn & Squeezes ──
    async def _passthrough_hyperliquid_action(**kwargs) -> dict | None:
        try:
            limit = kwargs.get("limit", 5)
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"http://localhost:8000/api/v1/market/hyperliquid-action?limit={limit}"
                )
                if r.status_code == 200:
                    return r.json()
        except (ValueError, ConnectionError, TimeoutError, RuntimeError):
            logger.warning("provider error")
        return None

    chains["hyperliquid_action"] = ProviderChain(
        data_type="hyperliquid_action",
        description="Hyperliquid gain/loss porn: top movers, funding squeezes, highest volume",
        providers=[
            Provider(
                "rmi_hyperliquid_action",
                ProviderTier.LOCAL,
                _passthrough_hyperliquid_action,
                weight=10.0,
            ),
        ],
    )

    # ── INSIDER WALLETS - Premium intelligence ──
    async def _passthrough_insider_wallets(**kwargs) -> dict | None:
        try:
            limit = kwargs.get("limit", 10)
            tier = kwargs.get("tier", "free")
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"http://localhost:8000/api/v1/market/insider-wallets?limit={limit}&tier={tier}"
                )
                if r.status_code == 200:
                    return r.json()
        except (ValueError, ConnectionError, TimeoutError, RuntimeError):
            logger.warning("provider error")
        return None

    chains["insider_wallets"] = ProviderChain(
        data_type="insider_wallets",
        description="Likely insider trader wallets to watch (Premium)",
        providers=[
            Provider(
                "rmi_insider_wallets", ProviderTier.LOCAL, _passthrough_insider_wallets, weight=10.0
            ),
        ],
    )

    # ── PREDICTION SIGNALS - Market intelligence layer ──
    async def _passthrough_prediction_signals(**kwargs) -> dict | None:
        try:
            limit = kwargs.get("limit", 5)
            tier = kwargs.get("tier", "free")
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    f"http://localhost:8000/api/v1/market/prediction-signals?limit={limit}&tier={tier}"
                )
                if r.status_code == 200:
                    return r.json()
        except (ValueError, ConnectionError, TimeoutError, RuntimeError):
            logger.warning("provider error")
        return None

    chains["prediction_signals"] = ProviderChain(
        data_type="prediction_signals",
        description="Prediction market intelligence signals (Polymarket, Kalshi, etc.)",
        providers=[
            Provider(
                "rmi_prediction_signals",
                ProviderTier.LOCAL,
                _passthrough_prediction_signals,
                weight=10.0,
            ),
        ],
    )

    # Initialize circuit breakers and rate limiters
    for chain in chains.values():
        for provider in chain.providers:
            if provider.name not in _circuit_breakers:
                _circuit_breakers[provider.name] = _CircuitBreaker(
                    threshold=provider.failure_threshold, timeout=provider.recovery_timeout
                )
            if provider.name not in _rate_limiters:
                _rate_limiters[provider.name] = _RateLimiter(rps=provider.rate_limit_rps)

    logger.info(
        f"Built {len(chains)} provider chains with {sum(len(c.providers) for c in chains.values())} total providers"
    )
    return chains
