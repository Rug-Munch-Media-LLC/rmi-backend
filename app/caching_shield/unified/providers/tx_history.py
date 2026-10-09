"""Transaction history providers for unified data layer."""

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("unified_data_providers.tx_history")


class TxHistoryProviders:
    """Transaction history provider implementations."""

    async def _helius_txs(self, address: str, **kw: Any) -> dict[str, Any | None] | None:
        key = os.getenv("HELIUS_API_KEY", "")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://mainnet.helius-rpc.com/?api-key={key}",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "getSignaturesForAddress",
                    "params": [address, {"limit": 20}],
                },
            )
            if r.status_code == 200:
                return {"signatures": r.json().get("result", [])}
        return None

    async def _st_wallet(self, address: str, **kw: Any) -> Any:
        from app.caching_shield.solana_tracker import get_solana_tracker

        r = await get_solana_tracker().get_wallet(address)
        return {"total_value": r.get("total")} if r else None

    async def _publicnode_txs(self, address: str, **kw: Any) -> Any:
        return await self._helius_txs(address)
