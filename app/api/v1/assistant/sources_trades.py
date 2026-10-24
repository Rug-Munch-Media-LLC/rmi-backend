"""Trades page context: recent fills, large trades, maker concentration.

Fills come from Birdeye (the same lane live_trades uses) so the assistant API
does not import a router module.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from app.api.v1.assistant.models import PageParams, Section
from app.core.logging import get_logger

logger = get_logger(__name__)

_TIMEOUT = 20.0
_LARGE_TRADE_USD = 10_000.0


def maker_concentration(trades: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Share of traded USD per maker — proxy for maker concentration."""
    if not trades:
        return None
    volume: dict[str, float] = defaultdict(float)
    for trade in trades:
        volume[str(trade.get("maker") or "unknown")] += float(trade.get("amount_usd") or 0)
    total = sum(volume.values())
    ranked = sorted(volume.values(), reverse=True)
    return {
        "unique_makers": len(volume),
        "top_maker_share_pct": round(ranked[0] / total * 100, 2) if total > 0 else None,
        "top5_share_pct": round(sum(ranked[:5]) / total * 100, 2) if total > 0 else None,
        "trade_count": len(trades),
        "basis": "maker field of fetched fills",
    }


def _shape(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "tx_hash": item.get("txHash", item.get("signature", "")),
        "timestamp": item.get("blockUnixTime", item.get("timestamp", "")),
        "side": "buy" if str(item.get("side", "")).lower() in ("buy", "b") else "sell",
        "price_usd": float(item.get("priceUsd", item.get("price", 0)) or 0),
        "amount_token": float(item.get("amount", item.get("tokenAmount", 0)) or 0),
        "amount_usd": float(item.get("volumeUSD", item.get("valueUsd", 0)) or 0),
        "maker": item.get("from", item.get("maker", item.get("wallet", ""))),
        "source": "birdeye",
    }


async def _fetch_trades(token: str, limit: int) -> list[dict[str, Any]]:
    """Fetch recent fills from Birdeye."""
    from app.birdeye_client import BirdeyeClient

    payload = await BirdeyeClient()._call(
        "/defi/v2/token/trades", {"address": token, "limit": limit, "offset": 0}
    )
    if not isinstance(payload, dict):
        return []
    items = (payload.get("data") or {}).get("items") or []
    return [_shape(item) for item in items if isinstance(item, dict)]


def _compact(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "tx_hash": trade.get("tx_hash", ""),
        "timestamp": trade.get("timestamp", ""),
        "side": trade.get("side"),
        "price_usd": trade.get("price_usd"),
        "amount_usd": trade.get("amount_usd"),
        "maker": trade.get("maker", ""),
        "source": trade.get("source", ""),
    }


def _sections(trades: list[dict[str, Any]]) -> dict[str, Section]:
    large = [t for t in trades if float(t.get("amount_usd") or 0) >= _LARGE_TRADE_USD]
    reason = None if trades else "no_trade_data"
    return {
        "recent_fills": Section(
            source="birdeye./defi/v2/token/trades",
            data={"count": len(trades), "fills": [_compact(t) for t in trades[:10]]} if trades else None,
            reason=reason,
        ),
        "large_trades": Section(
            source="birdeye./defi/v2/token/trades",
            data={
                "threshold_usd": _LARGE_TRADE_USD,
                "count": len(large),
                "trades": [_compact(t) for t in large[:5]],
            }
            if trades
            else None,
            reason=reason,
        ),
        "maker_concentration": Section(
            source="derived:fills.maker",
            data=maker_concentration(trades),
            reason=reason,
        ),
    }


async def build_trades(params: PageParams) -> dict[str, Section]:
    """Build trades sections from Birdeye fills."""
    try:
        trades = await asyncio.wait_for(
            _fetch_trades(params.token, params.limit), timeout=_TIMEOUT
        )
    except Exception as e:
        logger.warning("page_context_trades_failed: %s", e)
        trades = []
    return _sections(trades)
