#!/usr/bin/env python3
"""CryptoScamDB fetching (GitHub urls.yaml primary, public API fallback).

Returns the parsed payload; the caller maps it to rows.
"""

from __future__ import annotations

from typing import Any

import httpx

from .logging_setup import logger, yaml

__all__ = ["fetch_cryptoscamdb_entries", "fetch_cryptoscamdb_payload"]

GITHUB_URL = "https://raw.githubusercontent.com/CryptoScamDB/blacklist/master/data/urls.yaml"
API_URLS = [
    "https://api.cryptoscamdb.org/v1/scams",
    "https://api.cryptoscamdb.org/v1/all",
]


async def fetch_cryptoscamdb_entries() -> list[dict[str, Any]]:
    """Fetch + parse the GitHub urls.yaml blacklist; [] when unavailable."""
    if yaml is None:
        logger.error("  pyyaml not installed — cannot parse CryptoScamDB YAML")
        return []
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(GITHUB_URL)
            if resp.status_code != 200:
                logger.warning(f"  CryptoScamDB GitHub returned {resp.status_code}")
                return []
            entries = yaml.safe_load(resp.text)
    except Exception as e:
        logger.warning(f"  CryptoScamDB GitHub fetch failed: {e}")
        return []
    if not isinstance(entries, list):
        return []
    logger.info(f"  Got {len(entries)} entries from GitHub urls.yaml")
    return [e for e in entries if isinstance(e, dict)]


async def fetch_cryptoscamdb_payload() -> Any:
    """Fallback: public API (intermittently 502). Returns parsed JSON or None."""
    async with httpx.AsyncClient(timeout=30) as client:
        for url in API_URLS:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    logger.info(f"  Got response from {url}")
                    return resp.json()
                logger.warning(f"  {url} returned {resp.status_code}")
            except Exception as e:
                logger.debug(f"  {url} failed: {e}")
    return None
