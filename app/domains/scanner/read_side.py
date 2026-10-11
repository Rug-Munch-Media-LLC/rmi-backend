"""Standalone scanner service client — invocation producer for rugmunch-scanner.

The standalone rugmunch-scanner service runs on loopback (127.0.0.1:8098) and exposes:
- POST /scan  — body {"token": "<address>", "chain": "<chain>"}
- GET /chains — lists supported chains

This module provides a fail-open client: any exception, non-200, or status != "ok"
returns None so the main scan response is never affected.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def fetch_read_side(token: str, chain: str) -> dict[str, Any] | None:
    """Call the standalone scanner service and return trimmed read-side signals.

    Args:
        token: Token address to scan.
        chain: Chain identifier (e.g., "ethereum", "solana").

    Returns:
        Trimmed dict with keys: status, risk_score, risk_level, signals,
        corroboration, confidence_tier, coverage, duration_ms — or None on
        any failure (timeout, connection error, non-200, status != "ok").
    """
    url = settings.scanner_service_url
    if not url:
        return None

    # Ensure no trailing slash
    base = url.rstrip("/")
    endpoint = f"{base}/scan"

    payload = {"token": token, "chain": chain}
    timeout = httpx.Timeout(20.0, connect=5.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json=payload)
    except httpx.TimeoutException as exc:
        logger.debug("read_side_scan_timeout", extra={"token": token, "chain": chain, "error": str(exc)})
        return None
    except httpx.ConnectError as exc:
        logger.debug("read_side_scan_connection_error", extra={"token": token, "chain": chain, "error": str(exc)})
        return None
    except Exception as exc:
        logger.debug("read_side_scan_unexpected_error", extra={"token": token, "chain": chain, "error": str(exc)})
        return None

    if resp.status_code != 200:
        logger.debug(
            "read_side_scan_non_200",
            extra={"token": token, "chain": chain, "status_code": resp.status_code},
        )
        return None

    try:
        data = resp.json()
    except Exception as exc:
        logger.debug("read_side_scan_invalid_json", extra={"token": token, "chain": chain, "error": str(exc)})
        return None

    if data.get("status") != "ok":
        logger.debug("read_side_scan_status_not_ok", extra={"token": token, "chain": chain, "status": data.get("status")})
        return None

    # Trim to the allowed keys
    allowed_keys = {
        "status",
        "risk_score",
        "risk_level",
        "signals",
        "corroboration",
        "confidence_tier",
        "coverage",
        "duration_ms",
    }
    trimmed = {k: data.get(k) for k in allowed_keys}
    logger.info("read_side_scan_merged", extra={"token": token, "chain": chain, "risk_level": trimmed.get("risk_level")})
    return trimmed


__all__ = ["fetch_read_side"]
