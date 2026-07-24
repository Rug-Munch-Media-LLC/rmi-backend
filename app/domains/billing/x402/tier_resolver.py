"""Tier resolver — determines a caller's payment tier and trial eligibility.

Extracted from enforcement.py (P6 - complexity refactor).

Responsibility: developer API key validation, trial checking/consumption,
burst rate limiting. Wraps Redis-backed counters.
"""

from __future__ import annotations

import os
from typing import Any

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger("x402_enforcement")


def check_trial(tool_id: str, client_id: str, max_trials: int = 3) -> tuple[bool, int]:
    """Check if a client has remaining trial calls for a tool.

    Returns (can_use, remaining) tuple.
    """
    try:
        r = get_redis()
        if not r:
            return True, max_trials

        key = f"x402:trial:{client_id}:{tool_id}"
        used_raw = r.get(key)
        used = int(used_raw) if used_raw else 0

        if used >= max_trials:
            ttl = r.ttl(key)
            remaining = 0 if ttl > 0 else max_trials
            return False, remaining

        return True, max_trials - used
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"Trial check failed: {e}")
        return True, max_trials


def consume_trial(tool_id: str, client_id: str, max_trials: int = 3) -> bool:
    """Record a trial usage. Increments counter, sets TTL on first use."""
    try:
        r = get_redis()
        if not r:
            return False

        key = f"x402:trial:{client_id}:{tool_id}"
        used_raw = r.get(key)
        if used_raw is None:
            r.setex(key, 86400, "1")
        else:
            r.incr(key)
        return True
    except (ConnectionError, OSError, TimeoutError, httpx.HTTPError, httpx.TimeoutException) as e:
        logger.error(f"Trial consume failed: {e}")
        return False


async def check_developer_key(request: Any) -> dict[str, Any] | None:
    """Validate a developer API key attached to the request.

    Returns None if no dev key present, or tier info dict from the
    developer_tier module. Handles ImportError gracefully.
    """
    try:
        from app.routers.developer_tier import check_developer_key as _check

        return await _check(request)
    except ImportError:
        return None
    except (ValueError, TypeError, KeyError, OSError) as e :
        logger.debug("check_developer_key_error", exc_info=True)
        logger.warning(f"Developer key check failed: {e}")
        return None


def check_burst_limit(client_id: str, tool_id: str) -> dict[str, Any] | None:
    """Enforce burst rate limiting (max N requests per tool per client per window).

    Returns None if under limit, or a result dict with status_code/message.
    """
    r = get_redis()
    if not r:
        return None

    burst_key = f"x402:burst:{client_id}:{tool_id}"
    try:
        BURST_LIMIT = int(os.getenv("X402_BURST_LIMIT", "5"))
        BURST_WINDOW = int(os.getenv("X402_BURST_WINDOW", "60"))
        pipe = r.pipeline()
        pipe.incr(burst_key)
        if not r.exists(burst_key):
            pipe.expire(burst_key, BURST_WINDOW)
        results = pipe.execute()
        burst_count = results[0] if results else 0
        if burst_count > BURST_LIMIT:
            logger.warning(
                f"Burst limit: {client_id} hit {burst_count} requests in {BURST_WINDOW}s"
            )
            return {
                "status_code": 429,
                "message": f"Rate limited. Max {BURST_LIMIT} requests per {BURST_WINDOW}s per tool.",
                "retry_after": BURST_WINDOW,
            }
        return None
    except (ConnectionError, OSError, TimeoutError):
        return None
