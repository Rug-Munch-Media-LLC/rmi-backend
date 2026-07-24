"""Shared HTTP client for async requests.

This module provides a singleton httpx.AsyncClient that should be used
for all external API calls to avoid connection pool fragmentation.
"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None

async def get_http_client() -> httpx.AsyncClient:
    """Get the shared httpx.AsyncClient instance.

    Creates the client on first call with sensible defaults:
    - 100 max connections
    - 20 max keepalive connections
    - 10 second timeout
    """
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            timeout=httpx.Timeout(timeout=10.0, connect=5.0, read=5.0, write=5.0),
            follow_redirects=True,
        )
    return _client

async def close_http_client() -> None:
    """Close the shared httpx.AsyncClient.

    Should be called on shutdown to clean up connections.
    """
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None

# Re-export for convenience
from httpx import Limits, Response, Timeout  # noqa: E402

__all__ = ["Limits", "Response", "Timeout", "close_http_client", "get_http_client"]
