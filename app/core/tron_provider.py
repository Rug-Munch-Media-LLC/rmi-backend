"""Tron blockchain provider - TronGrid API (keyed free tier).

TRONGRID_API_KEY env upgrades from anonymous limits to the registered
free tier (higher req/s + daily quota). Falls back keyless if unset.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)
TRONGRID = "https://api.trongrid.io"


def _headers() -> dict[str, str]:
    key = os.getenv("TRONGRID_API_KEY", "").strip()
    return {"TRON-PRO-API-KEY": key} if key else {}


async def tron_balance(address: str) -> dict[str, Any] | None:
    """Get TRX balance + TRC20 tokens for an address."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            # TRX balance
            r = await c.get(f"{TRONGRID}/v1/accounts/{address}", headers=_headers())
            if r.status_code == 200:
                data = r.json().get("data", [{}])[0]
                balance_trx = data.get("balance", 0) / 1_000_000

                # TRC20 tokens
                r2 = await c.get(
                    f"{TRONGRID}/v1/accounts/{address}/transactions/trc20", params={"limit": 1}, headers=_headers()
                )
                tokens = []
                if r2.status_code == 200:
                    for t in r2.json().get("data", [])[:1]:
                        tokens.append(
                            {
                                "token": t.get("token_info", {}).get("symbol", "?"),
                                "address": t.get("token_info", {}).get("address", ""),
                            }
                        )

                return {
                    "address": address,
                    "balance_trx": round(balance_trx, 2),
                    "bandwidth": data.get("free_net_usage", 0),
                    "energy": data.get("account_resource", {}).get("energy_usage", 0),
                    "trc20_tokens": tokens,
                    "provider": "trongrid",
                }
    except Exception as e:
        logger.warning(f"Tron balance failed: {e}")
    return None


async def tron_transactions(address: str, limit: int = 10) -> dict[str, Any] | None:
    """Get recent transactions for an address."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{TRONGRID}/v1/accounts/{address}/transactions", params={"limit": limit})
            if r.status_code == 200:
                txs = r.json().get("data", [])
                return {
                    "address": address,
                    "transactions": [
                        {
                            "tx_id": tx.get("txID", "")[:16],
                            "block": tx.get("blockNumber", 0),
                            "timestamp": tx.get("block_timestamp", 0),
                        }
                        for tx in txs[:limit]
                    ],
                    "count": len(txs),
                    "provider": "trongrid",
                }
    except (ConnectionError, OSError, TimeoutError) as e:
        logger.warning(f"tron_transactions failed: {e}")
    return None
