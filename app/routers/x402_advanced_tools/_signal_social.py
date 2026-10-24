"""Social-anomaly signal for ``rug_probability``.

Extracted verbatim from the router: more than 20 CryptoPanic mentions for the
address prefix is worth 5 points and one `low` reason. Failures score nothing.
"""

import json
from typing import Any
from urllib.parse import quote

import aiohttp

from app.core.logging import get_logger

logger = get_logger("x402.rug_probability")


async def social_hits(addr: str) -> list[tuple[int, dict[str, Any]]]:
    """Return ``(points, signal)`` when social mentions spike above 20."""
    symbol = addr[:12]
    url = f"https://cryptopanic.com/api/free/posts/?filter=important&q={quote(symbol)}"
    try:
        async with (
            aiohttp.ClientSession() as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp,
        ):
            if resp.status != 200:
                return []
            data = await resp.json()
    except (TimeoutError, aiohttp.ClientError, json.JSONDecodeError) as e:
        logger.debug(f"CryptoPanic failed: {e}")
        return []

    posts = data.get("results", [])
    if not posts or len(posts) <= 20:
        return []
    return [
        (
            5,
            {
                "signal": "social_spike",
                "band": "low",
                "detail": f"{len(posts)} social mentions",
            },
        )
    ]


__all__ = ["social_hits"]
