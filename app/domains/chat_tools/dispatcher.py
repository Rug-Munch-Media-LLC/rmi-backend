"""Dispatcher for the chat tool-use layer.

Maps a detected intent to its executor and runs it with a hard timeout.
Fail-open: returns ``None`` on unknown intent, executor error, or timeout.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.logging import get_logger

from .market import _exec_market
from .news import _exec_news
from .perp import _exec_perp
from .prediction import _exec_prediction
from .price import _exec_price
from .scan import _exec_scan
from .suspicious import _exec_suspicious
from .ta import _exec_ta
from .trace import _exec_trace

logger = get_logger(__name__)

TOOL_TIMEOUT_SECONDS = 8.0

_EXECUTORS: dict[str, Any] = {
    "price": _exec_price,
    "scan": _exec_scan,
    "trace": _exec_trace,
    "ta": _exec_ta,
    "market": _exec_market,
    "news": _exec_news,
    "suspicious": _exec_suspicious,
    "prediction": _exec_prediction,
    "perp": _exec_perp,
}


async def _run_tool_for_intent(message: str, intent: str) -> str | None:
    """Run the executor for a detected intent with a hard timeout. Fail-open.

    Returns None on unknown intent, executor error, or timeout — callers
    fall through to normal RAG/LLM. Never raises.
    """
    executor = _EXECUTORS.get(intent)
    if executor is None:
        return None
    try:
        results = await asyncio.wait_for(
            asyncio.gather(executor(message)),
            timeout=TOOL_TIMEOUT_SECONDS,
        )
    except Exception:
        logger.debug("chat_tool_executor_failed", intent=intent)
        return None
    result = results[0] if results else None
    return result if isinstance(result, str) and result.strip() else None
