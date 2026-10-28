"""DEX-parity full token data: the cascading ``get_token_full`` orchestrator."""

from __future__ import annotations

from typing import Any

from app.providers.base import ProviderBase, logger
from app.providers.dex_sources import DexSourcesMixin
from app.providers.enrichment import EnrichmentMixin
from app.providers.pricing import PricingMixin
from app.providers.token_full_merge import (
    merge_dexscreener,
    merge_metadata,
    merge_price,
    token_full_skeleton,
)


class TokenFullMixin(PricingMixin, DexSourcesMixin, EnrichmentMixin, ProviderBase):
    """Comprehensive token payload assembled from five cascading sources."""

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

        result = token_full_skeleton(mint, chain)

        # Phase 1: Get metadata and basic price in parallel

        try:
            import asyncio

            # Check scanner cache first (Redis)
            scanner_data = await self._get_scanner_cache(mint, chain)
            if scanner_data:
                result.update(scanner_data)
                result["source_chain"].append("scanner_cache")

            # Fire parallel fetches
            tasks: list[asyncio.Task[Any]] = []
            tasks.append(asyncio.create_task(self.get_token_metadata(mint)))
            tasks.append(asyncio.create_task(self.get_token_price(mint)))
            tasks.append(asyncio.create_task(self._fetch_dexscreener_full(mint, chain)))

            meta, price, dex_data = await asyncio.gather(*tasks, return_exceptions=True)

            # Merge metadata, price, then DexScreener data
            merge_metadata(result, meta)
            merge_price(result, price)
            merge_dexscreener(result, dex_data)

            # Phase 2 + 3: premium enrichment and OHLCV
            await self._enrich_premium(result, mint, chain)
            await self._enrich_ohlcv(result, mint, chain)

            # Cache full result for 60s
            await cache.set("full", result, mint, ttl=60)

        except Exception as e:
            logger.warning("get_token_full failed: %s", e)

        return result

    async def _enrich_premium(self, result: dict[str, Any], mint: str, chain: str) -> None:
        """GMGN (holders, smart money, trades) then Birdeye (holders, security)."""
        try:
            gmgn_data = await self._fetch_gmgn_enrichment(mint, chain)
            if gmgn_data:
                for key in ["holders", "buy_24h", "sell_24h", "top_holders", "smart_money"]:
                    if gmgn_data.get(key) is not None and result.get(key) is None:
                        result[key] = gmgn_data[key]
                result["source_chain"].append("gmgn")
        except Exception as e:
            logger.warning("GMGN enrichment error: %s", e)

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

    async def _enrich_ohlcv(self, result: dict[str, Any], mint: str, chain: str) -> None:
        """OHLCV from GeckoTerminal (free) when the payload has none."""
        if not result.get("ohlcv"):
            try:
                ohlcv = await self._fetch_geckoterminal_ohlcv(mint, chain)
                if ohlcv:
                    result["ohlcv"] = ohlcv
                    result["source_chain"].append("geckoterminal")
            except Exception as e:
                logger.warning("GeckoTerminal OHLCV error: %s", e)
