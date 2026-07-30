"""Token Intelligence v2 — composite endpoint aggregating all DataBus layers."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v2/tokens", tags=["token-intelligence-v2"])


@router.get("/{address}/intelligence")
async def token_intelligence(address: str, chain: str = Query(default="solana")) -> dict[str, Any]:
    """Return composite token intelligence — all DataBus layers in one call."""
    start = time.perf_counter()

    async def _fetch(name: str, coro) -> Any | dict:
        try:
            return await asyncio.wait_for(coro, timeout=15.0)
        except Exception as e:
            return {"error": str(e)[:200]}

    # DataBus provider imports — lazy to avoid startup dependency
    from app.databus.providers import (
        fetch_dev_reputation,
        fetch_holder_health,
        fetch_ohlcv,
        fetch_rug_patterns,
        fetch_token_trades,
        fetch_top_traders,
        fetch_volume_authenticity,
    )

    args = {"address": address, "chain": chain}

    results = await asyncio.gather(
        _fetch("volume_authenticity", fetch_volume_authenticity(**args)),
        _fetch("dev_reputation", fetch_dev_reputation(**args)),
        _fetch("holder_health", fetch_holder_health(**args)),
        _fetch("rug_patterns", fetch_rug_patterns(**args)),
        _fetch("token_trades", fetch_token_trades(**args)),
        _fetch("top_traders", fetch_top_traders(**args)),
        _fetch("ohlcv", fetch_ohlcv(**args)),
        return_exceptions=True,
    )

    keys = [
        "volume_authenticity",
        "dev_reputation",
        "holder_health",
        "rug_patterns",
        "token_trades",
        "top_traders",
        "ohlcv",
    ]

    response: dict[str, Any] = {
        "token": address,
        "chain": chain,
        "scanned_at": int(time.time()),
        "elapsed_ms": int((time.perf_counter() - start) * 1000),
    }
    for key, result in zip(keys, results, strict=False):
        if isinstance(result, Exception):
            response[key] = {"error": str(result)[:200]}
        else:
            response[key] = result

    return response
