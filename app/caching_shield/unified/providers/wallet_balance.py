"""Wallet balance providers for unified data layer."""

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("unified_data_providers.wallet_balance")


class WalletBalanceProviders:
    """Wallet balance provider implementations."""

    async def _helius_balance(self, address: str, **kw: Any) -> dict[str, Any] | None:
        from app.consensus_rpc import get_consensus_rpc

        r = await get_consensus_rpc().get_balance(address)
        if r and r.value:
            v = r.value if isinstance(r.value, (int, float)) else r.value.get("value", 0)
            return {"balance_lamports": v}
        return None

    async def _quicknode_balance(self, address: str, **kw: Any) -> Any:
        return await self._helius_balance(address)

    async def _alchemy_balance(self, address: str, **kw: Any) -> Any:
        return await self._helius_balance(address)

    async def _publicnode_balance(self, address: str, **kw: Any) -> dict[str, Any | None] | None:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.post(
                "https://solana-rpc.publicnode.com",
                json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]},
            )
            if r.status_code == 200:
                return {"balance_lamports": r.json().get("result", {}).get("value", 0)}
        return None
