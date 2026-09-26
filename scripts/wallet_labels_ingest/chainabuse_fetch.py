#!/usr/bin/env python3
"""ChainAbuse HTTP fan-out: paginated primary endpoint + alt-endpoint fallback."""

from __future__ import annotations

from typing import Any

import httpx

from .logging_setup import logger

__all__ = ["fetch_reports"]

API_URL = "https://api.chainabuse.com/v1/reports"
ALT_URLS = [
    "https://api.chainabuse.com/v0/reports",
    "https://chainabuse.com/api/reports",
]
PAGE_SIZE = 100
MAX_PAGES = 3


def _extract_reports(data: Any) -> list[Any]:
    """Pull the report list out of a bare-list or wrapped API response."""
    if isinstance(data, list):
        return data
    return data.get("data", data.get("reports", [])) if isinstance(data, dict) else []


async def _fetch_primary(client: httpx.AsyncClient) -> list[Any]:
    """Walk up to MAX_PAGES of the primary endpoint, stopping early when short."""
    all_reports: list[Any] = []
    for page in range(MAX_PAGES):
        url = f"{API_URL}?limit={PAGE_SIZE}&offset={page * PAGE_SIZE}"
        try:
            resp = await client.get(url, headers={"Accept": "application/json"})
        except Exception as e:
            logger.debug(f"  ChainAbuse page {page} failed: {e}")
            break
        if resp.status_code == 200:
            reports = _extract_reports(resp.json())
            if reports:
                all_reports.extend(reports)
                logger.info(f"  Page {page + 1}: {len(reports)} reports (total: {len(all_reports)})")
            if len(reports) < PAGE_SIZE:
                break
        elif resp.status_code == 401:
            logger.warning("  ChainAbuse requires auth - trying alt endpoints")
            break
        else:
            logger.warning(f"  ChainAbuse returned {resp.status_code}")
            break
    return all_reports


async def _fetch_alternates(client: httpx.AsyncClient) -> list[Any]:
    """Try each alternate endpoint until one returns reports."""
    for alt_url in ALT_URLS:
        try:
            resp = await client.get(alt_url)
        except Exception as exc:
            logger.debug(f"Alternate ChainAbuse endpoint failed: {exc}")
            continue
        if resp.status_code == 200:
            reports = _extract_reports(resp.json())
            if reports:
                logger.info(f"  Got {len(reports)} from alt endpoint: {alt_url}")
                return reports
    return []


async def fetch_reports() -> list[Any]:
    """Fetch ChainAbuse reports, falling back to alternate endpoints."""
    async with httpx.AsyncClient(timeout=30) as client:
        reports = await _fetch_primary(client)
        return reports or await _fetch_alternates(client)
