"""Proxy-aware httpx client factory for X/Twitter fetches.

Call sites stay one line:

    async with x_http_client(timeout=15, cookies=cookies) as client:
        ...

Every client shares the process-wide :class:`ProxyHealth` breaker and the
process-wide token bucket, so rate limiting and health accounting cover the
whole X fetch tier instead of one client at a time.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.databus.x_intel.x_proxy.health import ProxyHealth
from app.databus.x_intel.x_proxy.ratelimit import TokenBucket
from app.databus.x_intel.x_proxy.source import get_proxy_candidates
from app.databus.x_intel.x_proxy.transport import RotatingProxyTransport

# Shared across every client built by this factory.
health = ProxyHealth()
limiter = TokenBucket()

_BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"


def x_http_client(
    *,
    timeout: float = 15.0,
    cookies: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.AsyncClient:
    """Build an X fetch client that rotates WebShare proxies then goes direct.

    ``headers`` are merged over browser-ish defaults so X does not serve a
    bot-challenge page to an obviously scripted client.
    """
    merged = {"Accept": _BROWSER_ACCEPT, "Accept-Language": "en-US,en;q=0.9"}
    if headers:
        merged.update(headers)
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
        cookies=cookies,
        headers=merged,
        follow_redirects=True,
        transport=RotatingProxyTransport(candidates=get_proxy_candidates, health=health, bucket=limiter),
    )
