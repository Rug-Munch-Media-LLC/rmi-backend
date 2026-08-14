"""Shared async HTTP client factory for the RugMunchBot tool registry.

All tools use httpx.AsyncClient — never the blocking ``requests`` / urllib
synchronous path — so the bot event loop is never blocked by a data call.
Each client is created per-call with a bounded timeout (no shared mutable
client, no leaked connections).
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_TIMEOUT = 8.0


def make_client(timeout: float = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
    """Create an isolated AsyncClient with connect/read/write timeouts."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=3.0, read=timeout, write=8.0),
        follow_redirects=True,
        headers={"User-Agent": "RugMunchBot/1.0"},
    )


async def http_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any] | None:
    """GET a URL and parse JSON. Returns None on any failure (never raises)."""
    try:
        async with make_client(timeout) as client:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                return None
            body = r.json()
            return body if isinstance(body, dict) else None
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError):
        return None


async def http_post_json(
    url: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any] | None:
    """POST JSON and parse the response. Returns None on any failure."""
    try:
        async with make_client(timeout) as client:
            r = await client.post(url, json=json or {}, headers=headers)
            if r.status_code != 200:
                return None
            body = r.json()
            return body if isinstance(body, dict) else None
    except (httpx.HTTPError, ConnectionError, OSError, TimeoutError, ValueError):
        return None
