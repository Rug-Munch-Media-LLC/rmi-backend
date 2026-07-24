"""
Free MCP Server Integrations - Boar Blockchain, Solana Token Analysis, autonsol

Adds free, keyless MCP data sources to the caching shield fallback engine.
All three require NO API keys - pure free data.

Boar Blockchain (50 tools): EVM data - balances, txs, blocks, ENS, ERC-20
Solana Token Analysis (6 tools): Risk scoring 0-100, pump.fun signals, momentum
autonsol/sol-mcp: Real-time token risk + rug flags

Usage:
    from app.caching_shield.mcp_sources import MCPDataSources
    mcp = MCPDataSources()
    score = await mcp.solana_risk_score("EPjFWd...")
    balance = await mcp.evm_balance("0x...", chain_id=1)
"""

from __future__ import annotations

import json
import time

import httpx

from app.core.logging import get_logger

logger = get_logger("mcp_sources")

# Smithery MCP endpoints (HTTP transport)
BOAR_URL = "https://server.smithery.ai/@boar-network/blockchain-advanced/mcp"
SOLANA_TOKEN_URL = "https://server.smithery.ai/@insomniactools/solana-agentkit-mcp/mcp"
AUTONSOL_URL = "https://server.smithery.ai/..."  # placeholder - need exact URL

# Cache
_l1: dict[str, tuple] = {}
_cache_hits = 0
_cache_misses = 0

class MCPDataSources:
    """Wraps free MCP servers as cached data sources."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=15.0,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "rmi-caching-shield/1.0",
                },
            )
        return self._http

    async def _mcp_call(self, url: str, tool: str, args: dict, ttl: int = 60) -> dict | None:
        """Make a cached MCP tool call."""
        global _cache_hits, _cache_misses
        cache_key = f"mcp:{tool}:{json.dumps(args, sort_keys=True)}"

        # L1 cache
        entry = _l1.get(cache_key)
        if entry:
            expiry, data = entry
            if time.monotonic() < expiry:
                _cache_hits += 1
                return data
            del _l1[cache_key]
        _cache_misses += 1

        http = await self._get_http()
        try:
            # MCP JSON-RPC call
            r = await http.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": args},
                },
            )
            if r.status_code == 200:
                data = r.json()
                result = data.get("result", {}).get("content", [{}])[0].get("text", "")
                if result:
                    try:
                        parsed = json.loads(result)
                    except (json.JSONDecodeError):
                        parsed = {"value": str(result)[:500]}
                    _l1[cache_key] = (time.monotonic() + ttl, parsed)
                    return parsed
        except Exception as e:
            logger.debug(f"MCP {tool} error: {e}")
        return None

    # ═══════════════════════════════════════════════════════════════════════
    # BOAR BLOCKCHAIN - EVM Data (50 tools, FREE, keyless)
    # ═══════════════════════════════════════════════════════════════════════

    async def evm_balance(self, address: str, chain: str = "ethereum") -> dict | None:
        """Get ETH/native balance. Free, no key."""
        return await self._mcp_call(
            BOAR_URL,
            "get_balance",
            {
                "address": address,
                "chain": chain,
            },
            ttl=15,
        )

    async def evm_transactions(
        self, address: str, chain: str = "ethereum", limit: int = 10
    ) -> dict | None:
        """Get recent transactions."""
        return await self._mcp_call(
            BOAR_URL,
            "get_transactions",
            {
                "address": address,
                "chain": chain,
                "limit": limit,
            },
            ttl=30,
        )

    async def evm_contract_info(self, address: str, chain: str = "ethereum") -> dict | None:
        """Get contract/ERC-20 info."""
        return await self._mcp_call(
            BOAR_URL,
            "get_contract_info",
            {
                "address": address,
                "chain": chain,
            },
            ttl=300,
        )

    async def ens_resolve(self, name: str) -> dict | None:
        """Resolve ENS name to address."""
        return await self._mcp_call(
            BOAR_URL,
            "resolve_ens",
            {
                "name": name,
            },
            ttl=3600,
        )

    async def evm_block_info(
        self, block_number: int | None = None, chain: str = "ethereum"
    ) -> dict | None:
        """Get block info."""
        args = {"chain": chain}
        if block_number:
            args["block_number"] = block_number
        return await self._mcp_call(BOAR_URL, "get_block", args, ttl=60)

    # ═══════════════════════════════════════════════════════════════════════
    # SOLANA TOKEN ANALYSIS - Risk Scoring (6 tools, NO AUTH)
    # ═══════════════════════════════════════════════════════════════════════

    async def solana_risk_score(self, mint: str) -> dict | None:
        """Get token risk score 0-100. Free, no auth."""
        return await self._mcp_call(
            SOLANA_TOKEN_URL,
            "analyze_token",
            {
                "token_address": mint,
            },
            ttl=60,
        )

    async def solana_momentum(self, mint: str) -> dict | None:
        """Get BUY/SELL momentum signals."""
        return await self._mcp_call(
            SOLANA_TOKEN_URL,
            "get_momentum",
            {
                "token_address": mint,
            },
            ttl=15,
        )

    async def solana_batch_screen(self, mints: list[str]) -> dict | None:
        """Batch screen multiple tokens for risk."""
        return await self._mcp_call(
            SOLANA_TOKEN_URL,
            "batch_screen",
            {
                "token_addresses": mints[:10],
            },
            ttl=45,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # AUTONSOL/SOL-MCP - Rug Detection (FREE)
    # ═══════════════════════════════════════════════════════════════════════

    async def rug_check(self, mint: str) -> dict | None:
        """Real-time rug pull detection with flags."""
        return await self._mcp_call(
            AUTONSOL_URL,
            "check_token",
            {
                "mint": mint,
            },
            ttl=60,
        )

    # ═══════════════════════════════════════════════════════════════════════
    # STATS
    # ═══════════════════════════════════════════════════════════════════════

    def stats(self) -> dict:
        return {
            "cache_hits": _cache_hits,
            "cache_misses": _cache_misses,
            "sources": {
                "boar_blockchain": "50 tools, EVM, FREE keyless",
                "solana_token_analysis": "6 tools, risk scoring, NO AUTH",
                "autonsol": "rug detection, FREE",
            },
        }

# Singleton
_mcp: MCPDataSources | None = None

def get_mcp_sources() -> MCPDataSources:
    global _mcp
    if _mcp is None:
        _mcp = MCPDataSources()
    return _mcp
