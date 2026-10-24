"""Bounded JSON GET used by every trade source.

Contract: never raises, always returns ``None`` on failure, and never retries
more than once (only on 429, honouring a short fixed backoff). Callers must
treat ``None`` as "this source has no data", never as an empty trade list.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

TIMEOUT_S = 20.0
RETRY_AFTER_CAP_S = 5.0
_UA = "rmi-live-trades/1.0 (+https://rugmunch.io)"


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    source: str = "unknown",
) -> Any | None:
    """GET ``url`` returning parsed JSON, or None. Rate limits are not hidden."""
    hdrs = {"accept": "application/json", "user-agent": _UA, **(headers or {})}
    for attempt in (1, 2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=True) as client:
                resp = await client.get(url, params=params, headers=hdrs)
            if resp.status_code == 429:
                wait = RETRY_AFTER_CAP_S if attempt == 1 else 0
                logger.warning("trade_source_rate_limited", source=source, attempt=attempt)
                if wait:
                    await asyncio.sleep(wait)
                    continue
                return None
            if resp.status_code != 200:
                logger.warning("trade_source_http_error", source=source, status=resp.status_code)
                return None
            return resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("trade_source_failed", source=source, error=type(exc).__name__)
            return None
    return None
