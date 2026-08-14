"""TA executor for the chat tool-use layer.

Fetches candles + computes TA — prefers the TA domain's ``ta_summary``
(available via the package shim; the domain may be absent while a parallel
agent builds it) and falls back to an inline RSI + change summary.
Fail-open: returns ``None`` on any error/timeout and NEVER raises.
"""

from __future__ import annotations

import inspect

from app.core.logging import get_logger

from ._shared import _to_float

logger = get_logger(__name__)


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder-smoothed RSI over closing prices."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _inline_ta_text(symbol: str, closes: list[float], current: float, change: float) -> str:
    rsi = _rsi(closes) if len(closes) >= 2 else None
    rsi_str = f"RSI(14): {rsi:.1f}" if rsi is not None else "RSI: n/a"
    return f"{symbol} ${current:,.4g} (24h {change:+.1f}%). {rsi_str}."


async def _exec_ta(message: str) -> str | None:
    """Fetch candles + compute TA (ta_summary when available, else inline RSI)."""
    # Patch-sensitive shared helpers + the optional TA-domain hooks resolve
    # through the package shim at call time so tests can mock them on
    # app.domains.chat_tools.* (lazy to avoid an import cycle — the shim
    # imports this module).
    from app.domains.chat_tools import (
        _chain_for_address,
        _extract_first_address,
        _extract_price_symbol,
        ta_summary,
        ta_summary_text,
    )

    symbol = _extract_price_symbol(message)
    if not symbol:
        return None
    address = _extract_first_address(message)
    chain = _chain_for_address(address) if address else "ethereum"
    token = address or symbol.lower()
    try:
        from app.databus.ohlcv_engine import fetch_ohlcv

        data = await fetch_ohlcv(token=token, chain=chain, timeframe="1h", limit=48)
        if not data or not data.get("candles"):
            return None
        candles = [c for c in data["candles"] if isinstance(c, dict)]
        if not candles:
            return None
        summary = data.get("summary") or {}
        current = _to_float(summary.get("current_price")) or _to_float(candles[-1].get("close")) or 0.0
        if not current:
            return None
        change = _to_float(summary.get("price_change_pct")) or 0.0
        closes = [_to_float(c.get("close")) for c in candles if c.get("close") is not None]
        closes = [c for c in closes if c is not None]

        # Prefer the TA domain's summary when available (parallel agent);
        # fall back to inline RSI + change so TA questions still get numbers.
        if ta_summary is not None:
            try:
                summary_obj = (
                    ta_summary(candles, symbol=symbol)
                    if not inspect.iscoroutinefunction(ta_summary)
                    else await ta_summary(candles, symbol=symbol)
                )
                if summary_obj and ta_summary_text is not None:
                    text = ta_summary_text(summary_obj)
                    if inspect.isawaitable(text):
                        text = await text
                    if isinstance(text, str) and text.strip():
                        return text
            except Exception as e:
                logger.debug("chat_tool_ta_summary_failed", error=str(e)[:120])
        return _inline_ta_text(symbol.upper(), closes, current, change)
    except Exception as e:
        logger.debug("chat_tool_ta_failed", error=str(e)[:120])
        return None
