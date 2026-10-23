"""Transport classifier for GetXAPI reads.

A provider failure must never be reported as an empty result: the caller uses
the error code to decide whether a miss is cachable. Codes are stable strings
(`transport`, `invalid_json`, `invalid_payload`, `http_<status>`) so logs and
health checks can key on them without parsing prose.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from app.core.logging import get_logger

logger = get_logger("shill.getxapi")

REQUEST_TIMEOUT_S = 15.0


async def request_json(url: str, params: dict[str, Any], api_key: str) -> tuple[dict[str, Any] | None, str]:
    """GET one GetXAPI resource. Returns (payload, error); error "" is success."""
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {api_key}"},
            )
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("getxapi_transport_failed url=%s error=%s", url, str(exc)[:120])
        return None, "transport"

    if response.status_code != 200:
        # 402 = out of credits, 401/403 = bad key, 429 = throttled, 5xx = provider.
        # None of these mean "this handle has no content".
        logger.warning("getxapi_http_status url=%s status=%s", url, response.status_code)
        return None, f"http_{response.status_code}"

    try:
        payload: object = response.json()
    except ValueError:
        logger.warning("getxapi_invalid_json url=%s", url)
        return None, "invalid_json"

    if not isinstance(payload, dict):
        logger.warning("getxapi_invalid_payload url=%s type=%s", url, type(payload).__name__)
        return None, "invalid_payload"
    return cast("dict[str, Any]", payload), ""


__all__ = ["request_json"]
