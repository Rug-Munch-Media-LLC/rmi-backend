"""RugCharts page context: OHLCV market summary, TA, key levels.

Candles come from the DataBus OHLCV engine (own data) — no router imports,
so the assistant API keeps the api -> routers boundary intact.
"""

from __future__ import annotations

from typing import Any

from app.api.v1.assistant.models import PageParams, Section
from app.core.logging import get_logger
from app.databus.ohlcv_engine import fetch_ohlcv
from app.services.ta_indicators import compute_ta_snapshot

logger = get_logger(__name__)


def key_levels(candles: list[dict[str, Any]], ta: dict[str, Any]) -> dict[str, Any] | None:
    """Derive support/resistance levels from the candle window."""
    if not candles:
        return None
    recent = candles[-20:]
    return {
        "period_high": max(float(c["high"]) for c in candles),
        "period_low": min(float(c["low"]) for c in candles),
        "range_high_20": max(float(c["high"]) for c in recent),
        "range_low_20": min(float(c["low"]) for c in recent),
        "sma_20": ta.get("sma_20"),
        "bb_upper": ta.get("bb_upper"),
        "bb_lower": ta.get("bb_lower"),
        "basis": "computed_from_ohlcv",
    }


def market_summary(candles: list[dict[str, Any]], timeframe: str) -> dict[str, Any] | None:
    if not candles:
        return None
    closes = [float(c["close"]) for c in candles]
    first = closes[0]
    return {
        "timeframe": timeframe,
        "current_price": closes[-1],
        "price_change_pct": round(((closes[-1] - first) / first * 100), 2) if first > 0 else None,
        "high": max(float(c["high"]) for c in candles),
        "low": min(float(c["low"]) for c in candles),
        "volume": sum(float(c.get("volume") or 0) for c in candles),
        "candle_count": len(candles),
        "candle_source": "databus.ohlcv_engine",
    }


async def _candles(params: PageParams) -> list[dict[str, Any]]:
    try:
        payload = await fetch_ohlcv(
            token=params.token,
            chain=params.chain,
            timeframe=params.timeframe,
            limit=params.limit,
        )
    except Exception as e:
        logger.warning("page_context_ohlcv_failed: %s", e)
        return []
    return list((payload or {}).get("candles") or [])


async def build_rugcharts(params: PageParams) -> dict[str, Section]:
    """Build rugcharts sections — each degrades to null+reason."""
    candles = await _candles(params)
    ta = compute_ta_snapshot(candles) if candles else {"available": False, "reason": "no_candles"}
    missing = "no_ohlcv_candles"
    return {
        "market": Section(
            source="databus.ohlcv_engine",
            data=market_summary(candles, params.timeframe),
            reason=None if candles else missing,
        ),
        "technical": Section(
            source="ta_indicators.compute_ta_snapshot",
            data=ta if ta.get("available") else None,
            reason=None if ta.get("available") else str(ta.get("reason") or "ta_unavailable"),
        ),
        "key_levels": Section(
            source="derived:ohlcv",
            data=key_levels(candles, ta),
            reason=None if candles else missing,
        ),
    }
