"""HTTP helper utilities for the honeypot detector."""
from __future__ import annotations

from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

async def fetch_with_fallback(urls: list[str], headers: dict[str, str] | None = None, timeout: int = 15, json_mode: bool = True) -> tuple[Any, str]:
    import httpx
    if headers is None: headers = {"User-Agent": "RMI-Honeypot/1.0"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in urls:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    return (resp.json(), url) if json_mode else (resp.text, url)
                logger.debug(f"HTTP {resp.status_code} from {url}")
            except Exception as e:
                logger.debug(f"Failed {url}: {e}")
                continue
    return {}, ""
