"""Risk scan providers for unified data layer."""

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("unified_data_providers.risk_scan")


class RiskScanProviders:
    """Risk scan provider implementations."""

    async def _goplus(self, address: str, chain: str = "solana", **kw: Any) -> dict[str, bool] | None:
        key = os.getenv("GOPLUS_API_KEY", "")
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"https://api.gopluslabs.io/api/v1/token_security/{chain}",
                params={"contract_addresses": address},
                headers={"Authorization": f"Bearer {key}"} if key else {},
            )
            if r.status_code == 200:
                d = r.json().get("result", {}).get(address.lower(), {})
                return {
                    "is_honeypot": d.get("is_honeypot") == "1",
                    "risk_score": 100 - int(d.get("trust_score", 50)),
                }
        return None

    async def _rugcheck(self, address: str, **kw: Any) -> dict[str, Any | None] | None:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.rugcheck.xyz/v1/tokens/{address}/report")
            if r.status_code == 200:
                d = r.json()
                return {
                    "score": d.get("score", 0),
                    "risks": [r.get("name") for r in d.get("risks", []) if r.get("score", 0) > 1000],
                }
        return None

    async def _honeypot(self, address: str, **kw: Any) -> dict[str, Any | None] | None:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.honeypot.is/v2/IsHoneypot?address={address}&chainID=1")
            if r.status_code == 200:
                return {"is_honeypot": r.json().get("honeypotResult", {}).get("isHoneypot", False)}
        return None

    async def _local_labels(self, address: str, **kw: Any) -> Any | None:
        try:
            from app.wallet_label_loader import lookup_wallet_label

            label = lookup_wallet_label(address)
            return {"label": label} if label else None
        except (AttributeError, ImportError, httpx.HTTPError):
            return None
