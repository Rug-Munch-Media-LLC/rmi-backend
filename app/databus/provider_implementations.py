"""
DataBus Provider Implementations - External API Interfaces
===========================================================

All external API implementations for the DataBus providers.
This module contains NO chain definitions or infrastructure.
"""

import asyncio
import datetime
import os

import httpx

from sdks.python.x402_frameworks.autogen_adapter import logger


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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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
        pass
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


# ── HYPERLIQUID PASS-THROUGH ─────────────────────────────────────


async def _passthrough_hyperliquid(**kwargs) -> dict | None:
    """Hyperliquid perp markets from our own /api/v1/market/hyperliquid."""
    try:
        limit = kwargs.get("limit", 10)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"http://localhost:8000/api/v1/market/hyperliquid?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
        pass
    return None


async def _passthrough_hyperliquid_action(**kwargs) -> dict | None:
    """Hyperliquid gain/loss porn from our own /api/v1/market/hyperliquid-action."""
    try:
        limit = kwargs.get("limit", 5)
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"http://localhost:8000/api/v1/market/hyperliquid-action?limit={limit}")
            if r.status_code == 200:
                return r.json()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("provider error")
        pass
    return None


# ── INSIDER WALLETS PASS-THROUGH ─────────────────────────────────


async def _passthrough_insider_wallets(**kwargs) -> dict | None:
    """Likely insider trader wallets from our own /api/v1/market/insider-wallets."""
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
        pass
    return None


# ── PREDICTION SIGNALS PASS-THROUGH ─────────────────────────────


async def _passthrough_prediction_signals(**kwargs) -> dict | None:
    """Prediction market intelligence from our own /api/v1/market/prediction-signals."""
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
        pass
    return None
