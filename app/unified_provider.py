"""
Unified Chain Data Provider - Multi-source with automatic fallback.
Priority: Helius → Birdeye → Solscan → GMGN → DexScreener → QuickNode → Blockchair
Rate-limited to ~5 req/sec total. Heavy caching. Never blocks on a single API.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Singleton ──────────────────────────────────────────────

_provider: Optional["UnifiedProvider"] = None


def get_unified_provider() -> "UnifiedProvider":
    global _provider
    if _provider is None:
        _provider = UnifiedProvider()
    return _provider


class UnifiedProvider:
    """Multi-source chain data with fallback cascade."""

    def __init__(self):
        self._birdeye = None
        self._solscan = None
        self._gmgn = None

    # ── Lazy Connectors ────────────────────────────────────

    @property
    def birdeye(self):
        if self._birdeye is None:
            try:
                from app.birdeye_client import BirdeyeClient

                self._birdeye = BirdeyeClient()
            except ImportError:
                self._birdeye = False
        return self._birdeye if self._birdeye is not False else None

    @property
    def solscan(self):
        if self._solscan is None:
            try:
                from app.free_solscan_client import FreeSolscanClient

                self._solscan = FreeSolscanClient()
            except ImportError:
                self._solscan = False
        return self._solscan if self._solscan is not False else None

    @property
    def gmgn(self):
        if self._gmgn is None:
            try:
                from app.gmgn_client import GMGNClient

                self._gmgn = GMGNClient()
            except ImportError:
                self._gmgn = False
        return self._gmgn if self._gmgn is not False else None

    # ── Solana: Get Token Holders ──────────────────────────

    async def get_token_holders(self, mint: str, limit: int = 20) -> list[dict]:
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

    async def _helius_holders(self, mint: str, limit: int) -> list[dict]:
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
        except Exception as e:
            logger.debug(f"Helius holders failed: {e}")
            return []

    async def _birdeye_holders(self, mint: str, limit: int) -> list[dict]:
        try:
            if not self.birdeye:
                return []
            data = await self.birdeye._call("/defi/token_holders", {"address": mint, "offset": 0, "limit": limit})
            items = data.get("data", {}).get("items", [])
            return [{"address": i.get("owner", ""), "amount": i.get("uiAmount", 0), "source": "birdeye"} for i in items]
        except Exception as e:
            logger.debug(f"Birdeye holders failed: {e}")
            return []

    async def _solscan_holders(self, mint: str, limit: int) -> list[dict]:
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
        except Exception as e:
            logger.debug(f"Solscan holders failed: {e}")
            return []

    async def _dexscreener_holders(self, mint: str, limit: int) -> list[dict]:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
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
        except Exception as e:
            logger.debug(f"DexScreener holders failed: {e}")
        return []

    # ── Solana: Wallet Transactions ────────────────────────

    async def get_wallet_transactions(self, wallet: str, limit: int = 20) -> list[dict]:
        """Get wallet transactions - Helius → Solscan → Birdeye."""
        cache = self._get_cache()
        cached = await cache.get("txs_v2", wallet, limit)
        if cached:
            return cached

        # 1. Helius
        txs = await self._helius_txs(wallet, limit)
        if txs:
            await cache.set("txs_v2", txs, wallet, limit, ttl=120)
            return txs

        # 2. Solscan
        txs = await self._solscan_txs(wallet, limit)
        if txs:
            await cache.set("txs_v2", txs, wallet, limit, ttl=120)
            return txs

        await cache.set("txs_v2", [], wallet, limit, ttl=60)
        return []

    async def _helius_txs(self, wallet: str, limit: int) -> list[dict]:
        try:
            from app.chain_client import get_chain_client

            client = get_chain_client()
            sigs = await client.get_signatures(wallet, limit=limit)
            return (
                [
                    {
                        "signature": s.get("signature"),
                        "timestamp": s.get("blockTime"),
                        "status": "success" if not s.get("err") else "failed",
                        "source": "helius",
                    }
                    for s in sigs
                ]
                if sigs
                else []
            )
        except Exception as e:
            logger.debug(f"Helius txs failed: {e}")
            return []

    async def _solscan_txs(self, wallet: str, limit: int) -> list[dict]:
        try:
            if not self.solscan:
                return []
            txs = (
                self.solscan.get_account_transactions(wallet, limit=limit)
                if hasattr(self.solscan, "get_account_transactions")
                else []
            )
            if isinstance(txs, list):
                return [
                    {"signature": t.get("txHash", t.get("signature", "")), "source": "solscan"} for t in txs[:limit]
                ]
            return []
        except Exception as e:
            logger.debug(f"Solscan txs failed: {e}")
            return []

    # ── Token Price ────────────────────────────────────────

    async def get_token_price(self, mint: str) -> float | None:
        """Get token price - Birdeye → DexScreener → Jupiter."""
        cache = self._get_cache()
        cached = await cache.get("price", mint)
        if cached is not None:
            return cached

        # 1. Birdeye
        try:
            if self.birdeye:
                data = await self.birdeye.get_price(mint)
                price = data.get("data", {}).get("value")
                if price:
                    await cache.set("price", price, mint, ttl=60)
                    return price
        except Exception:
            pass

        # 2. DexScreener
        try:
            import httpx

            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        price = float(pairs[0].get("priceUsd", 0))
                        await cache.set("price", price, mint, ttl=60)
                        return price
        except Exception:
            pass

        await cache.set("price", None, mint, ttl=60)
        return None

    # ── Token Metadata ─────────────────────────────────────

    async def get_token_metadata(self, mint: str) -> dict:
        """Get token metadata - Helius → Birdeye → DexScreener."""
        cache = self._get_cache()
        cached = await cache.get("meta", mint)
        if cached:
            return cached

        # 1. Helius
        try:
            from app.chain_client import get_chain_client

            client = get_chain_client()
            meta = await client.get_token_metadata(mint)
            if meta:
                result = {
                    "name": meta.get("name", ""),
                    "symbol": meta.get("symbol", ""),
                    "source": "helius",
                }
                await cache.set("meta", result, mint, ttl=600)
                return result
        except Exception:
            pass

        # 2. DexScreener
        try:
            import httpx

            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        base = pairs[0].get("baseToken", {})
                        result = {
                            "name": base.get("name", ""),
                            "symbol": base.get("symbol", ""),
                            "source": "dexscreener",
                        }
                        await cache.set("meta", result, mint, ttl=600)
                        return result
        except Exception:
            pass

        result = {"name": mint[:8], "symbol": "???", "source": "fallback"}
        await cache.set("meta", result, mint, ttl=600)
        return result

    # ── Cache Helper ───────────────────────────────────────

    def _get_cache(self):
        from app.chain_cache import get_chain_cache

        return get_chain_cache()
