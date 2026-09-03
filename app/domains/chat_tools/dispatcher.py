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
TOOL_MAX_RETRIES = 2
TOOL_RETRY_DELAYS = (1.0, 2.0)

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


async def _run_with_retry(
    coro: Any,
    max_retries: int = TOOL_MAX_RETRIES,
    timeout: float = TOOL_TIMEOUT_SECONDS,
) -> str | None:
    """Run *coro* with up to *max_retries* retries and a per-attempt timeout.

    Retry schedule:
        Try 1: direct call (timeout=*timeout*)
        Retry 1: wait 1 s, retry
        Retry 2: wait 2 s, retry

    Logs each retry attempt. Fail-open — returns ``None`` when all attempts
    have been exhausted. Never raises.
    """
    for attempt in range(1 + max_retries):
        try:
            result = await asyncio.wait_for(
                asyncio.gather(coro),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("chat_tool_retry_timeout attempt=%d/%d", attempt + 1, 1 + max_retries)
        except Exception:
            logger.warning("chat_tool_retry_error attempt=%d/%d", attempt + 1, 1 + max_retries)
        else:
            val = result[0] if result else None
            if val is not None and isinstance(val, str) and val.strip():
                return val
            logger.info("chat_tool_retry_empty attempt=%d/%d", attempt + 1, 1 + max_retries)

        # All attempts exhausted — do not sleep after the final failure.
        if attempt < max_retries:
            delay = TOOL_RETRY_DELAYS[attempt] if attempt < len(TOOL_RETRY_DELAYS) else 2.0
            logger.info("chat_tool_retry_sleep duration=%.1fs attempt=%d/%d", delay, attempt + 1, 1 + max_retries)
            await asyncio.sleep(delay)

    return None


async def _run_tool_for_intent(message: str, intent: str) -> str | None:
    """Run the executor for a detected intent with retry + timeout. Fail-open.

    Returns None on unknown intent, executor error, or timeout — callers
    fall through to normal RAG/LLM. Never raises.
    """
    executor = _EXECUTORS.get(intent)
    if executor is None:
        return None
    return await _run_with_retry(executor(message))
