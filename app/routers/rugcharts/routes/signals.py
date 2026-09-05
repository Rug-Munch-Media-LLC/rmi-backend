"""Trading signals built from labeled whale-trade data."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.core.logging import get_logger

from ..classifier import classify_trades, label_summary
from ..helpers import _fetch_whale_trades_clickhouse

logger = get_logger("rugcharts_router")

router = APIRouter(tags=["rugcharts"])


@router.get("/signals/{address}")
async def rugcharts_signals(
    address: str,
    chain: str = Query("solana", description="Blockchain to query"),
    limit: int = Query(20, ge=1, le=100, description="Max signals to return"),
) -> dict[str, Any]:
    """Trading signals for a token, built from labeled whale-trade data.

    Each signal derives from a real ClickHouse dex event and carries the
    classifier's wallet_label. When no trades resolve we return an empty
    list — the frontend renders empty gracefully.
    """
    trades = await _fetch_whale_trades_clickhouse(address, chain, limit)
    labeled = classify_trades(trades)
    signals: list[dict[str, Any]] = []
    for t in labeled:
        side = t.get("side", "unknown")
        label = t.get("wallet_label", "swap")
        confidence = max(t.get("label_confidence", 40), 70 if side != "unknown" else 40)
        signals.append(
            {
                "id": t.get("tx_hash", ""),
                "type": "buy" if side == "buy" else "sell" if side == "sell" else "info",
                "timestamp": t.get("time", 0),
                "price_at_signal": float(t.get("price_usd", 0) or 0),
                "confidence": confidence,
                "title": f"{label.capitalize()} {side.upper()} ${float(t.get('volume_usd', 0) or 0):,.0f}"
                if side != "unknown"
                else f"{label.capitalize()} activity ${float(t.get('volume_usd', 0) or 0):,.0f}",
                "reasoning": f"{label}-labeled swap on {chain} ({t.get('tx_hash', '')[:10]}...)",
                "indicators": ["whale_trade", label, {"volume_usd": float(t.get("volume_usd", 0) or 0)}],
                "wallet_label": label,
                "timeframe": "1h",
            }
        )
    return {
        "chain": chain,
        "address": address,
        "signals": signals[:limit],
        "count": len(signals[:limit]),
        "label_summary": label_summary(labeled),
    }
