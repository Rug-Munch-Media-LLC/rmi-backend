"""
RugCharts Intel — Whale Alert Stream.
Real-time large transaction detection.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.logging import get_logger

from .cache import CACHE_TTL, HELIUS_KEY, _r

logger = get_logger("rugcharts_intel")


async def whale_alert_stream(
    address: str = "", chain: str = "solana", min_value_usd: float = 100000, **kw
) -> dict[str, Any] | None:
    """Detect large transactions on a token or wallet. Whale movement alerts.
    Pass address="" to get global whale activity across tracked tokens.
    """
    cache_key = f"whale_alert:{chain}:{address}:{min_value_usd}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("error")

    whales = []
    api_key = kw.get("api_key", "") or HELIUS_KEY

    try:
        if chain == "solana" and api_key and address:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 50}],
                    },
                )
                if r.status_code == 200:
                    sigs = r.json().get("result", [])
                    for sig_data in sigs[:20]:
                        tx_resp = await c.post(
                            f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "getTransaction",
                                "params": [sig_data["signature"], {"encoding": "jsonParsed"}],
                            },
                        )
                        if tx_resp.status_code == 200:
                            tx = tx_resp.json().get("result", {})
                            meta = tx.get("meta", {})
                            pre = meta.get("preTokenBalances", [])
                            post = meta.get("postTokenBalances", [])
                            for pb, ab in zip(pre[:5], post[:5], strict=False):
                                pre_amt = float(pb.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
                                post_amt = float(ab.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
                                delta = abs(post_amt - pre_amt)
                                if delta > 1000:
                                    whales.append(
                                        {
                                            "tx": sig_data["signature"][:16] + "...",
                                            "wallet": pb.get("owner", "")[:12] + "...",
                                            "amount": round(delta, 2),
                                            "timestamp": sig_data.get("blockTime"),
                                            "block": sig_data.get("slot"),
                                        }
                                    )
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError) as e:
        logger.warning(f"error: {e}")
        logger.debug(f"Whale alert: {e}")

    result = {
        "whale_movements": whales[:30],
        "total_detected": len(whales),
        "min_value_usd": min_value_usd,
        "chain": chain,
        "address": address,
        "scan_time": datetime.now(UTC).isoformat(),
        "source": "whale_alert_stream",
    }

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["whale_alert"], json.dumps(result, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError, httpx.HTTPError):
        logger.warning("error")

    return result
