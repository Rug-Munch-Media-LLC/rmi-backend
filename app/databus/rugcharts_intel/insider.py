"""
RugCharts Intel — Insider Pattern Detector.
Pre-pump accumulation detection.
"""

import json
from collections import defaultdict
from datetime import UTC, datetime

import httpx

from app.core.logging import get_logger
from .cache import _r, CACHE_TTL, HELIUS_KEY

logger = get_logger("rugcharts_intel")


async def insider_pattern_detector(address: str = "", chain: str = "solana", **kw) -> dict | None:
    """Detect wallets that accumulated before major price movements.
    Pattern: large buys in the hours before a significant price increase.
    """
    if not address:
        return None

    cache_key = f"insider:{chain}:{address}"
    try:
        r = _r()
        cached = r.get(cache_key)
        if cached:
            r.close()
            return json.loads(cached)
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    api_key = kw.get("api_key", "") or HELIUS_KEY
    insider_signals = []

    try:
        if chain == "solana" and api_key:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.post(
                    f"https://mainnet.helius-rpc.com/?api-key={api_key}",
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "getSignaturesForAddress",
                        "params": [address, {"limit": 200}],
                    },
                )
                if r.status_code == 200:
                    sigs = r.json().get("result", [])
                    if len(sigs) < 20:
                        return {"insider_signals": [], "total": 0, "note": "Not enough history"}

                    windows = defaultdict(list)
                    for s in sigs:
                        bt = s.get("blockTime", 0)
                        if bt:
                            window = bt // 600
                            windows[window].append(s)

                    avg_tx_per_window = len(sigs) / max(len(windows), 1)
                    for window, tx_list in sorted(windows.items()):
                        if len(tx_list) > avg_tx_per_window * 3:
                            next_windows = [w for w in windows if w > window and w <= window + 6]
                            for nw in next_windows:
                                if len(windows[nw]) > avg_tx_per_window * 2:
                                    insider_signals.append(
                                        {
                                            "window_start": datetime.fromtimestamp(
                                                window * 600, tz=UTC
                                            ).isoformat(),
                                            "tx_count_spike": len(tx_list),
                                            "avg_tx_count": round(avg_tx_per_window, 1),
                                            "spike_multiplier": round(
                                                len(tx_list) / max(avg_tx_per_window, 1), 1
                                            ),
                                            "followed_by_continued_volume": True,
                                            "signal_strength": "HIGH"
                                            if len(tx_list) > avg_tx_per_window * 5
                                            else "MEDIUM",
                                        }
                                    )
                                    break
    except (ValueError, ConnectionError, TimeoutError, RuntimeError) as e:
        logger.warning(f"error: {e}")
        logger.debug(f"Insider detection: {e}")

    result = {
        "insider_signals": insider_signals[:10],
        "total_signals": len(insider_signals),
        "address": address,
        "chain": chain,
        "confidence": "HIGH"
        if len(insider_signals) >= 2
        else ("MEDIUM" if insider_signals else "LOW"),
        "scan_time": datetime.now(UTC).isoformat(),
        "source": "insider_pattern_detector",
    }

    try:
        r = _r()
        r.setex(cache_key, CACHE_TTL["insider"], json.dumps(result, default=str))
        r.close()
    except (ValueError, ConnectionError, TimeoutError, RuntimeError):
        logger.warning("error")

    return result
