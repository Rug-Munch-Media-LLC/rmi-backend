"""Token-holder lookups: Helius → Birdeye → Solscan → DexScreener cascade."""

from __future__ import annotations

from typing import Any

from app.providers.base import ProviderBase, logger


class HoldersMixin(ProviderBase):
    """Token holders with caching and four-source fallback."""

    async def get_token_holders(self, mint: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get token holders - tries 5 sources in cascade."""
        cache = self._get_cache()
        cached = await cache.get("holders_v2", mint, limit)
        if cached:
            return cached

        # 1. Helius RPC (fastest, most complete)
        holders = await self._helius_holders(mint, limit)
        if holders:
            await cache.set("holders_v2", holders, mint, limit, ttl=300)
            return holders

        # 2. Birdeye (good holder data)
        holders = await self._birdeye_holders(mint, limit)
        if holders:
            await cache.set("holders_v2", holders, mint, limit, ttl=300)
            return holders

        # 3. Solscan (free tier, reverse-engineered)
        holders = await self._solscan_holders(mint, limit)
        if holders:
            await cache.set("holders_v2", holders, mint, limit, ttl=300)
            return holders

        # 4. DexScreener (free, no API key)
        holders = await self._dexscreener_holders(mint, limit)
        if holders:
            await cache.set("holders_v2", holders, mint, limit, ttl=300)
            return holders

        await cache.set("holders_v2", [], mint, limit, ttl=120)
        return []

    async def _helius_holders(self, mint: str, limit: int) -> list[dict[str, Any]]:
        """Get token holders via getProgramAccounts (correct method)."""
        try:
            from app.chain_client import get_chain_client

            client = get_chain_client()
            # Use getProgramAccounts with memcmp filter on mint address
            result = await client.rpc_call(
                "getProgramAccounts",
                [
                    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    {
                        "encoding": "jsonParsed",
                        "filters": [{"dataSize": 165}, {"memcmp": {"offset": 0, "bytes": mint}}],
                    },
                ],
            )
            holders = []
            if result and "result" in result:
                for r in result["result"][:limit]:
                    info = r.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                    owner = info.get("owner")
                    if owner and owner != mint:
                        holders.append(
                            {
                                "address": owner,
                                "amount": info.get("tokenAmount", {}).get("uiAmount", 0),
                                "source": "helius",
                            }
                        )
            return holders
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug("Helius holders failed: %s", e)
            return []

    async def _birdeye_holders(self, mint: str, limit: int) -> list[dict[str, Any]]:
        try:
            if not self.birdeye:
                return []
            data = await self.birdeye._call("/defi/token_holders", {"address": mint, "offset": 0, "limit": limit})
            items = data.get("data", {}).get("items", [])
            return [{"address": i.get("owner", ""), "amount": i.get("uiAmount", 0), "source": "birdeye"} for i in items]
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug("Birdeye holders failed: %s", e)
            return []

    async def _solscan_holders(self, mint: str, limit: int) -> list[dict[str, Any]]:
        try:
            if not self.solscan:
                return []
            holders = (
                self.solscan.get_token_holders(mint, limit=limit) if hasattr(self.solscan, "get_token_holders") else []
            )
            if isinstance(holders, list):
                return [
                    {
                        "address": h.get("owner", h.get("address", "")),
                        "amount": h.get("amount", 0),
                        "source": "solscan",
                    }
                    for h in holders[:limit]
                ]
            return []
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug("Solscan holders failed: %s", e)
            return []

    async def _dexscreener_holders(self, mint: str, limit: int) -> list[dict[str, Any]]:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as http_client:
                r = await http_client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        return [
                            {
                                "address": mint,
                                "source": "dexscreener",
                                "liquidity": pairs[0].get("liquidity", {}).get("usd", 0),
                            }
                        ]
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("DexScreener holders failed: %s", e)
        return []
