"""Technical Analysis (TA) domain — pure indicator math + candle summaries.

This package is intentionally dependency-free (stdlib ``math``/``statistics``
only). It never fetches data: candle FETCHING lives in
``app.databus.ohlcv_engine``. Every function takes candles (or extracted
price series) as input and returns ``None`` instead of raising on short or
malformed input, so callers (chat bot, alerts, RAG) can treat output as
optional without try/except.

Public API:
    indicators.sma / ema / rsi / macd / atr / support_resistance / trend /
    pct_change
    summary.ta_summary / summary.ta_summary_text
"""

from __future__ import annotations

from app.domains.ta.indicators import (
    atr,
    ema,
    macd,
    pct_change,
    rsi,
    sma,
    support_resistance,
    trend,
)
from app.domains.ta.summary import ta_summary, ta_summary_text

__all__ = [
    "atr",
    "ema",
    "macd",
    "pct_change",
    "rsi",
    "sma",
    "support_resistance",
    "ta_summary",
    "ta_summary_text",
    "trend",
]
