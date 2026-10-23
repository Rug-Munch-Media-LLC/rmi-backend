"""Transparent proxy-rotating transport for X/Twitter HTTP fetches.

``RotatingProxyTransport`` is a plain ``httpx.AsyncBaseTransport``. On every
request it walks a few WebShare proxies, retrying on the next one when the
response is 403/429 (X blocks and rate limits) or the transport fails, and
finally falls back to a direct connection. Call sites keep building an
ordinary ``httpx.AsyncClient`` — only the transport differs, so nothing is
patched or wrapped at runtime.

The request body is read once up front (``await request.aread()``), which
makes replay across attempts safe for POST/streaming bodies as well as GET.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final

import httpx

from app.core.logging import get_logger
from app.databus.x_intel.x_proxy.health import ProxyHealth
from app.databus.x_intel.x_proxy.ratelimit import TokenBucket

log = get_logger(__name__)

# Responses that mean "this proxy is blocked or throttled" — rotate.
RETRY_STATUS: Final[frozenset[int]] = frozenset({401, 403, 407, 429, 503})
MAX_PROXY_ATTEMPTS: Final[int] = 3

TransportFactory = Callable[[str | None], httpx.AsyncBaseTransport]


class RotatingProxyTransport(httpx.AsyncBaseTransport):
    """Rotate WebShare proxies per attempt, then fall back to direct.

    ``candidates`` is awaited per request so an expired or empty proxy list is
    refreshed by the source's own TTL cache rather than pinned at import time.
    ``transport_factory`` is the injection seam used by tests.
    """

    def __init__(
        self,
        *,
        candidates: Callable[[], Awaitable[list[str]]],
        health: ProxyHealth | None = None,
        bucket: TokenBucket | None = None,
        max_attempts: int = MAX_PROXY_ATTEMPTS,
        transport_factory: TransportFactory | None = None,
    ) -> None:
        self._candidates = candidates
        self._health = health if health is not None else ProxyHealth()
        self._bucket = bucket if bucket is not None else TokenBucket()
        self._max_attempts = max(max_attempts, 1)
        self._factory: TransportFactory = transport_factory or (
            lambda proxy: httpx.AsyncHTTPTransport(proxy=proxy, retries=0)
        )
        self._direct = self._factory(None)
        self._pool: dict[str, httpx.AsyncBaseTransport] = {}
        self._index = 0

    def _transport_for(self, proxy: str) -> httpx.AsyncBaseTransport:
        """One cached transport per proxy (own connection pool)."""
        transport = self._pool.get(proxy)
        if transport is None:
            transport = self._factory(proxy)
            self._pool[proxy] = transport
        return transport

    async def _attempt(self, request: httpx.Request, proxy: str) -> httpx.Response | None:
        """Try one proxy; None means "rotate to the next one"."""
        try:
            response = await self._transport_for(proxy).handle_async_request(request)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            self._health.record_failure(proxy, type(exc).__name__)
            return None
        if response.status_code in RETRY_STATUS:
            await response.aclose()
            self._health.record_failure(proxy, f"http_{response.status_code}")
            return None
        self._health.record_success(proxy)
        return response

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Proxy attempts with rotation, then a direct fallback."""
        await request.aread()  # replay-safe: stream replaced by in-memory bytes

        if not self._health.circuit_open():
            proxies = await self._candidates()
            total = len(proxies)
            if total:
                for offset in range(min(self._max_attempts, total)):
                    proxy = proxies[(self._index + offset) % total]
                    await self._bucket.acquire()
                    response = await self._attempt(request, proxy)
                    if response is not None:
                        self._index = (self._index + offset + 1) % total
                        return response
                self._index = (self._index + min(self._max_attempts, total)) % total
                log.debug("proxy_tier_exhausted", attempted=min(self._max_attempts, total))

        return await self._direct.handle_async_request(request)

    async def aclose(self) -> None:
        """Close the direct transport and every pooled proxy transport."""
        await self._direct.aclose()
        for transport in self._pool.values():
            await transport.aclose()
        self._pool.clear()
