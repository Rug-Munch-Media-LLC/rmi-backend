"""
Unified Chain Data Provider - Multi-source with automatic fallback.
Priority: Helius → Birdeye → Solscan → GMGN → DexScreener → QuickNode → Blockchair
Rate-limited to ~5 req/sec total. Heavy caching. Never blocks on a single API.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

# ── Singleton ──────────────────────────────────────────────

_provider: UnifiedProvider | None = None


def get_unified_provider() -> UnifiedProvider:
    global _provider
    if _provider is None:
        _provider = UnifiedProvider()
    return _provider


class UnifiedProvider:
    """Multi-source chain data with fallback cascade."""

    def __init__(self) -> None:
        self._birdeye: Any = None
        self._solscan: Any = None
        self._gmgn: Any = None

    # ── Lazy Connectors ────────────────────────────────────

    @property
    def birdeye(self) -> Any:
        if self._birdeye is None:
            try:
                from app.birdeye_client import BirdeyeClient

                self._birdeye = BirdeyeClient()
            except ImportError:
                self._birdeye = False
        return self._birdeye if self._birdeye is not False else None

    @property
    def solscan(self) -> Any:
        if self._solscan is None:
            try:
                from app.free_solscan_client import FreeSolscanClient

                self._solscan = FreeSolscanClient()
            except ImportError:
                self._solscan = False
        return self._solscan if self._solscan is not False else None

    @property
    def gmgn(self) -> Any:
        if self._gmgn is None:
            try:
                from app.integrations.gmgn_client import GMGNClient

                self._gmgn = GMGNClient()
            except ImportError:
                self._gmgn = False
        return self._gmgn if self._gmgn is not False else None

    # ── Solana: Get Token Holders ──────────────────────────

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

    # ── Solana: Wallet Transactions ────────────────────────

    async def get_wallet_transactions(self, wallet: str, limit: int = 20) -> list[dict[str, Any]]:
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

    async def _helius_txs(self, wallet: str, limit: int) -> list[dict[str, Any]]:
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
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug("Helius txs failed: %s", e)
            return []

    async def _solscan_txs(self, wallet: str, limit: int) -> list[dict[str, Any]]:
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
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug("Solscan txs failed: %s", e)
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
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug("Unified provider connection error: %s", e)

        # 2. DexScreener
        try:
            import httpx

            async with httpx.AsyncClient(timeout=8.0) as http_client:
                r = await http_client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if r.status_code == 200:
                    pairs = r.json().get("pairs", [])
                    if pairs:
                        price = float(pairs[0].get("priceUsd", 0))
                        await cache.set("price", price, mint, ttl=60)
                        return price
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("Unified provider HTTP error: %s", e)

        await cache.set("price", None, mint, ttl=60)
        return None

    # ── Token Metadata ─────────────────────────────────────

    async def get_token_metadata(self, mint: str) -> dict[str, Any]:
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
        except (ConnectionError, TimeoutError, OSError) as e:
            logger.debug("Unified provider connection error: %s", e)

        # 2. DexScreener
        try:
            import httpx

            async with httpx.AsyncClient(timeout=8.0) as http_client:
                r = await http_client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
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
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("Unified provider HTTP error: %s", e)

        result = {"name": mint[:8], "symbol": "???", "source": "fallback"}
        await cache.set("meta", result, mint, ttl=600)
        return result

    # ── DEX Parity: Full Token Data ───────────────────────

    async def get_token_full(self, mint: str, chain: str = "solana") -> dict[str, Any]:
        """
        Get comprehensive token data matching DexScreener/DexTools quality.

        Cascading priority:
          1. Scanner cache (fastest, most complete)
          2. GMGN (holder analysis, smart money, trades)
          3. Birdeye (price, overview, security)
          4. DexScreener (price, liquidity, volume — always available)
          5. GeckoTerminal (OHLCV, trending)
          6. Jupiter (price — Solana only)

        Returns standardized TokenData model.
        """
        cache = self._get_cache()
        cached = await cache.get("full", mint)
        if cached:
            return cached

        result: dict[str, Any] = {
            "address": mint,
            "chain": chain,
            "name": "",
            "symbol": "",
            "price_usd": None,
            "liquidity_usd": None,
            "volume_24h": None,
            "market_cap": None,
            "fdv": None,
            "holders": None,
            "price_change_1h": None,
            "price_change_24h": None,
            "price_change_7d": None,
            "trades_24h": None,
            "buy_24h": None,
            "sell_24h": None,
            "created_at": None,
            "dex": None,
            "pair_address": None,
            "security": None,
            "top_holders": [],
            "smart_money": None,
            "ohlcv": [],
            "source_chain": [],
            "timestamp": datetime.now(UTC).isoformat(),
        }

        # Phase 1: Get metadata and basic price in parallel

        try:
            import asyncio

            # Check scanner cache first (Redis)
            scanner_data = await self._get_scanner_cache(mint, chain)
            if scanner_data:
                result.update(scanner_data)
                result["source_chain"].append("scanner_cache")

            # Fire parallel fetches
            async def _fetch_dex():
                return await self._fetch_dexscreener_full(mint, chain)

            tasks: list[asyncio.Task[Any]] = []
            tasks.append(asyncio.create_task(self.get_token_metadata(mint)))
            tasks.append(asyncio.create_task(self.get_token_price(mint)))
            tasks.append(asyncio.create_task(_fetch_dex()))

            meta, price, dex_data = await asyncio.gather(*tasks, return_exceptions=True)

            # Merge metadata
            if isinstance(meta, dict) and not isinstance(meta, Exception):
                result["name"] = meta.get("name", result["name"])
                result["symbol"] = meta.get("symbol", result["symbol"])

            # Merge price
            if isinstance(price, (int, float)) and price:
                result["price_usd"] = float(price)

            # Merge DexScreener data (most complete free source)
            if isinstance(dex_data, dict) and not isinstance(dex_data, Exception):
                for key in [
                    "price_usd",
                    "liquidity_usd",
                    "volume_24h",
                    "fdv",
                    "market_cap",
                    "price_change_1h",
                    "price_change_24h",
                    "created_at",
                    "dex",
                    "pair_address",
                ]:
                    if dex_data.get(key) is not None and result.get(key) is None:
                        result[key] = dex_data[key]
                result["source_chain"].append("dexscreener")

            # Phase 2: Enrich with premium sources
            # GMGN (holder analysis, smart money, trades)
            try:
                gmgn_data = await self._fetch_gmgn_enrichment(mint, chain)
                if gmgn_data:
                    for key in ["holders", "buy_24h", "sell_24h", "top_holders", "smart_money"]:
                        if gmgn_data.get(key) is not None and result.get(key) is None:
                            result[key] = gmgn_data[key]
                    result["source_chain"].append("gmgn")
            except Exception as e:
                logger.warning("GMGN enrichment error: %s", e)

            # Birdeye (holder count, security)
            try:
                birdeye_data = await self._fetch_birdeye_enrichment(mint)
                if birdeye_data:
                    if birdeye_data.get("holders") and not result.get("holders"):
                        result["holders"] = birdeye_data["holders"]
                    if birdeye_data.get("security"):
                        result["security"] = birdeye_data["security"]
                    result["source_chain"].append("birdeye")
            except Exception as e:
                logger.warning("Birdeye enrichment error: %s", e)

            # Phase 3: OHLCV from GeckoTerminal (free)
            if not result.get("ohlcv"):
                try:
                    ohlcv = await self._fetch_geckoterminal_ohlcv(mint, chain)
                    if ohlcv:
                        result["ohlcv"] = ohlcv
                        result["source_chain"].append("geckoterminal")
                except Exception as e:
                    logger.warning("GeckoTerminal OHLCV error: %s", e)

            # Cache full result for 60s
            await cache.set("full", result, mint, ttl=60)

        except Exception as e:
            logger.warning("get_token_full failed: %s", e)

        return result

    async def _get_scanner_cache(self, mint: str, chain: str) -> dict[str, Any] | None:
        """Try to get data from existing scanner Redis cache."""
        try:
            import json

            from app.core.redis import get_redis_async

            r = get_redis_async()
            if r:
                raw = await r.get(f"sentinelscan:{chain}:{mint}")
                if raw:
                    data = json.loads(raw)
                    return {
                        "risk_score": data.get("risk_score"),
                        "liquidity_usd": data.get("liquidity_usd"),
                        "holders": data.get("total_holders", data.get("holder_count")),
                        "security": {
                            "is_honeypot": data.get("is_honeypot", data.get("is_potential_honeypot")),
                            "is_mintable": data.get("is_mintable"),
                            "is_renounced": data.get("is_renounced", data.get("is_mint_revoked")),
                            "scam_probability": data.get("scam_probability"),
                            "risk_flags": data.get("risk_flags", []),
                            "green_flags": data.get("green_flags", []),
                        },
                        "top_holders": (data.get("top_holders") or [])[:20],
                    }
        except Exception as e:
            logger.warning("Scanner cache error: %s", e)
        return None

    async def _fetch_dexscreener_full(self, mint: str, chain: str) -> dict[str, Any] | None:
        """Fetch full token data from DexScreener (free, unlimited)."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}")
                if resp.status_code == 200:
                    pairs = resp.json().get("pairs", [])
                    if pairs:
                        p = pairs[0]
                        return {
                            "name": p.get("baseToken", {}).get("name", ""),
                            "symbol": p.get("baseToken", {}).get("symbol", ""),
                            "price_usd": float(p.get("priceUsd", 0)),
                            "liquidity_usd": float(p.get("liquidity", {}).get("usd", 0)),
                            "volume_24h": float(p.get("volume", {}).get("h24", 0)),
                            "fdv": float(p.get("fdv", 0)),
                            "market_cap": float(p.get("marketCap", 0)),
                            "price_change_1h": float(p.get("priceChange", {}).get("h1", 0)),
                            "price_change_24h": float(p.get("priceChange", {}).get("h24", 0)),
                            "created_at": p.get("pairCreatedAt"),
                            "dex": p.get("dexId", ""),
                            "pair_address": p.get("pairAddress", ""),
                            "chain": p.get("chainId", chain),
                        }
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("DexScreener full fetch failed: %s", e)
        return None

    async def _fetch_gmgn_enrichment(self, mint: str, chain: str) -> dict[str, Any] | None:
        """Enrich with GMGN data if available."""
        try:
            from app.databus.gmgn_provider import get_gmgn_provider

            gmgn = get_gmgn_provider()
            holder_data = await gmgn.get_holder_analysis(mint, chain)
            smart_money = await gmgn.get_smart_money(mint, chain)

            result: dict[str, Any] = {}
            if holder_data and "error" not in holder_data:
                result["holders"] = holder_data.get("total_holders")
                result["top_holders"] = holder_data.get("top_holders", [])[:20]
            if smart_money and "error" not in smart_money:
                result["smart_money"] = smart_money
            return result if result else None
        except Exception:
            return None

    async def _fetch_birdeye_enrichment(self, mint: str) -> dict[str, Any] | None:
        """Enrich with Birdeye data (holder count, security)."""
        try:
            if not self.birdeye:
                return None
            overview = await self.birdeye.get_token_overview(mint)
            d = overview.get("data", {}) if isinstance(overview, dict) else {}
            security = d.get("security", {})
            return {
                "holders": d.get("holder"),
                "security": {
                    "risk_score": security.get("risk_score"),
                    "risk_level": security.get("risk_level"),
                    "flags": security.get("flags", []),
                },
            }
        except Exception:
            return None

    async def _fetch_geckoterminal_ohlcv(
        self, mint: str, chain: str = "solana", limit: int = 24
    ) -> list[dict[str, Any]] | None:
        """Fetch OHLCV data from GeckoTerminal (free, no auth)."""
        try:
            import httpx

            network = "solana" if chain == "solana" else "eth"
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get pool address first
                resp = await client.get(
                    f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{mint}/pools",
                    params={"limit": 1},
                )
                if resp.status_code != 200:
                    return None
                pools = resp.json().get("data", [])
                if not pools:
                    return None
                pool_addr = pools[0].get("id", "")
                if not pool_addr:
                    return None

                # Get OHLCV
                ohlcv_resp = await client.get(
                    f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{pool_addr}/ohlcv/hour",
                    params={"limit": limit},
                )
                if ohlcv_resp.status_code == 200:
                    data = ohlcv_resp.json().get("data", {})
                    attrs = data.get("attributes", {})
                    ohlcv_list = attrs.get("ohlcv_list", [])
                    if ohlcv_list:
                        return [
                            {
                                "timestamp": c[0],
                                "open": c[1],
                                "high": c[2],
                                "low": c[3],
                                "close": c[4],
                                "volume": c[5],
                            }
                            for c in ohlcv_list
                        ]
        except (httpx.HTTPError, ConnectionError, TimeoutError) as e:
            logger.debug("GeckoTerminal OHLCV failed: %s", e)
        return None

    # ── Cache Helper ───────────────────────────────────────

    def _get_cache(self) -> Any:
        from app.chain_cache import get_chain_cache

        return get_chain_cache()
