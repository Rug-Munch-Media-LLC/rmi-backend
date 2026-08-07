"""
Honeypot Detector — HTTP utilities
Async fetch with retry and fallback across multiple URLs.
"""

from __future__ import annotations

import json
from typing import Any

import requests

from app.core.logging import get_logger

logger = get_logger(__name__)


async def fetch_with_fallback(
    urls: list[str],
    headers: dict[str, str] | None = None,
    timeout: int = 15,
    json_mode: bool = True,
) -> tuple[Any, str]:
    """Try URLs in order, return first successful response."""
    import httpx

    if headers is None:
        headers = {"User-Agent": "RMI-Honeypot/1.0"}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in urls:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    if json_mode:
                        return resp.json(), url
                    return resp.text, url
                logger.debug(f"HTTP {resp.status_code} from {url}")
            except (
                httpx.HTTPError,
                requests.RequestException,
                json.JSONDecodeError,
                KeyError,
                TypeError,
                ConnectionError,
                OSError,
                TimeoutError,
            ) as e:
                logger.debug("fetch_with_fallback_error", exc_info=True)
                logger.debug(f"Failed {url}: {e}")
                continue
    return {}, ""
