"""
X402 Tool Data Provider - Cached, rate-limited data access for all x402 tools.

Replace raw aiohttp/httpx calls with this provider. One import, everything cached.

Usage in x402 routers:
    from app.caching_shield.tool_data import td

    # Instead of: async with aiohttp.ClientSession() as s: r = await s.get(url)
    # Use: result = await td.token_price(mint="So111...")
    # Returns: {"price_usd": 79.5, "source": "jupiter", "cached": False}
"""

from __future__ import annotations

from typing import Any

from app.caching_shield.unified_layer import get_data_layer


class ToolData:
    """Cached, rate-limited data provider for x402 tool routers."""

    def __init__(self) -> None:
        self._layer = get_data_layer()

    async def token_price(self, mint: str) -> dict[str, Any]:
        r = await self._layer.fetch("token_price", mint=mint)
        return r.to_dict() if r else {"error": "no data"}

    async def token_meta(self, mint: str) -> dict[str, Any]:
        r = await self._layer.fetch("token_meta", mint=mint)
        return r.to_dict() if r else {"error": "no data"}

    async def wallet_balance(self, address: str) -> dict[str, Any]:
        r = await self._layer.fetch("wallet_balance", address=address)
        return r.to_dict() if r else {"error": "no data"}

    async def risk_scan(self, address: str, chain: str = "solana") -> dict[str, Any]:
        r = await self._layer.fetch("risk_scan", address=address, chain=chain)
        return r.to_dict() if r else {"error": "no data"}

    async def tx_history(self, address: str) -> dict[str, Any]:
        r = await self._layer.fetch("tx_history", address=address)
        return r.to_dict() if r else {"error": "no data"}

    async def funding_source(self, address: str, chain_id: int = 1) -> dict[str, Any]:
        r = await self._layer.fetch("funding_source", address=address, chain_id=chain_id)
        return r.to_dict() if r else {"error": "no data"}

    async def call_tool(self, tool_id: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Generic tool dispatcher - calls unified_layer.fetch with tool_id and params.

        This is the primary method for trial execution and MCP tool calls.
        Falls back to specific ToolData methods for known tools, or uses
        the unified layer's generic fetch for all 127 tools.

        Returns None if the tool execution fails (so middleware can fall back
        to POST route handlers).
        """
        params = params or {}

        # Fallback: try specific methods for common tools
        method_map: dict[str, Any] = {
            "risk_scan": self.risk_scan,
            "token_price": self.token_price,
            "token_meta": self.token_meta,
            "wallet_balance": self.wallet_balance,
            "tx_history": self.tx_history,
            "funding_source": self.funding_source,
        }
        if tool_id in method_map:
            try:
                result = await method_map[tool_id](**params)
                # If the result has only an error key, it's not real data - return None
                if isinstance(result, dict) and set(result.keys()) <= {"error"}:
                    return None
                return result
            except (ConnectionError, OSError, TimeoutError):
                pass

        try:
            r = await self._layer.fetch(tool_id, **params)
            if r:
                result = r.to_dict()
                result["tool"] = tool_id
                # If result has only an error key, it's not real data - return None
                if set(result.keys()) <= {"error", "tool"}:
                    return None
                return result
        except (ConnectionError, OSError, TimeoutError):
            pass

        # Tool not available via DataBus - return None so middleware falls back
        return None

    def stats(self) -> dict[str, Any]:
        return self._layer.stats()


# Singleton
td = ToolData()
