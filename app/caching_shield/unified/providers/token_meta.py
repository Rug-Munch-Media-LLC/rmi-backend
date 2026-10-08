"""Token metadata providers for unified data layer."""

from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("unified_data_providers.token_meta")


class TokenMetaProviders:
    """Token metadata provider implementations."""

    async def _helius_das(self, mint: str, **kw: Any) -> dict[str, Any | None] | None:
        from app.caching_shield.helius_das import get_helius_das

        r = await get_helius_das().get_token_metadata(mint)
        if r:
            c = r.get("content", {}).get("metadata", {})
            t = r.get("token_info", {})
            return {"name": c.get("name"), "symbol": c.get("symbol"), "decimals": t.get("decimals")}
        return None

    async def _st_token(self, mint: str, **kw: Any) -> Any:
        from app.caching_shield.solana_tracker import get_solana_tracker

        r = await get_solana_tracker().get_token(mint)
        return {"name": r.get("token", {}).get("name")} if r else None

    async def _jupiter_list(self, mint: str, **kw: Any) -> dict[str, Any | None] | None:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get("https://token.jup.ag/strict")
            if r.status_code == 200:
                for t in r.json():
                    if t.get("address") == mint:
                        return {"name": t.get("name"), "symbol": t.get("symbol")}
        return None