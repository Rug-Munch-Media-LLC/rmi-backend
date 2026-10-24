"""HTTP JSON fetch for keyless chart-data sources with one shared reason taxonomy."""

from __future__ import annotations

from typing import Any

import httpx

from .common import logger

# Why a chart is empty must be machine-readable: "rate_limited" is a retry,
# "geo_blocked" is a dead source, "not_found" is a dead pool.
REASON_TIMEOUT = "timeout"
REASON_RATE_LIMITED = "rate_limited"
REASON_GEO_BLOCKED = "geo_blocked"
REASON_NOT_FOUND = "not_found"
REASON_HTTP_ERROR = "http_error"
REASON_NETWORK = "network_error"
REASON_BAD_JSON = "bad_json"
REASON_UNSUPPORTED = "unsupported_asset"
REASON_EMPTY = "empty_payload"


def _reason_for_status(status: int) -> str:
    """Map an HTTP status onto the reason taxonomy."""
    if status == 429:
        return REASON_RATE_LIMITED
    if status in (403, 451):
        return REASON_GEO_BLOCKED
    if status == 404:
        return REASON_NOT_FOUND
    return REASON_HTTP_ERROR


async def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 12.0,
) -> tuple[Any, str | None]:
    """GET ``url`` and return ``(payload, reason)`` — exactly one is None.

    Never raises: a dead free API degrades one chart, not the endpoint.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException:
        logger.warning("chart source timeout: %s", url)
        return None, REASON_TIMEOUT
    except httpx.HTTPError as exc:
        logger.warning("chart source network error: %s (%s)", url, str(exc)[:100])
        return None, REASON_NETWORK
    if response.status_code != 200:
        reason = _reason_for_status(response.status_code)
        logger.debug("chart source %s -> %s", url, reason)
        return None, reason
    try:
        return response.json(), None
    except ValueError:
        logger.warning("chart source bad json: %s", url)
        return None, REASON_BAD_JSON
