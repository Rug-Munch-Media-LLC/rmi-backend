"""Funding source providers for unified data layer."""

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("unified_data_providers.funding_source")


class FundingSourceProviders:
    """Funding source provider implementations (EVM)."""

    async def _blockscout(self, address: str, chain_id: int = 1, **kw: Any) -> dict[str, Any] | None:
        from app.caching_shield.funding_tracer import trace_funding_source

        r = await trace_funding_source(address, chain_id)
        if r and r.source_address:
            return {"source": r.source_address, "type": r.source_type, "confidence": r.confidence}
        return None

    async def _etherscan(self, address: str, chain_id: int = 1, **kw: Any) -> dict[str, int] | None:
        key = os.getenv("ETHERSCAN_API_KEY", "")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                "https://api.etherscan.io/api",
                params={
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "apikey": key,
                    "page": 1,
                    "offset": 5,
                },
            )
            if r.status_code == 200 and r.json().get("status") == "1":
                return {"txs": len(r.json().get("result", []))}
        return None

    async def _evm_rpc(self, address: str, chain_id: int = 1, **kw: Any) -> dict[str, Any] | None:
        from app.consensus_rpc import get_consensus_rpc

        r = await get_consensus_rpc().evm_query_with_consensus(chain_id, "eth_getBalance", [address, "latest"])
        if r and r.value:
            return {"balance": int(r.value, 16) / 1e18 if isinstance(r.value, str) else r.value}
        return None
