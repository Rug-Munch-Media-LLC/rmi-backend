"""Honeypot signal for ``rug_probability``.

Extracted verbatim from the router: a confirmed honeypot is worth 40 points and
one ``extreme`` reason. Failures are logged and scored as no signal, exactly as
before the split.
"""

import json
from typing import Any

import aiohttp

from app.config import settings
from app.core.logging import get_logger

logger = get_logger("x402.rug_probability")


async def honeypot_hits(addr: str, chain: str) -> list[tuple[int, dict[str, Any]]]:
    """Return ``(points, signal)`` for a confirmed honeypot, else no hits."""
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.post(
                f"{settings.backend_url}/api/v1/x402-tools/honeypot_check",
                json={"address": addr, "chain": chain},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp,
        ):
            if resp.status != 200:
                return []
            data = await resp.json()
            if not data.get("is_honeypot"):
                return []
            return [
                (
                    40,
                    {
                        "signal": "honeypot_detected",
                        "band": "extreme",
                        "detail": data.get("reason", "Cannot sell - confirmed honeypot"),
                    },
                )
            ]
    except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
        logger.debug(f"Honeypot check failed: {e}")
        return []


__all__ = ["honeypot_hits"]
