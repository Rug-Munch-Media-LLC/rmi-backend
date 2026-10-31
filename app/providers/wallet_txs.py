"""Wallet transaction lookups: Helius → Solscan."""

from __future__ import annotations

from typing import Any

from app.providers.base import ProviderBase, logger


class WalletTxMixin(ProviderBase):
    """Recent wallet transactions with caching and fallback."""

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
