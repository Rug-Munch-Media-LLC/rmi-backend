"""Tests for the X/WebShare proxy tier.

Covers rotation on 403/429/timeout, the direct fallback, the circuit breaker,
credential-free health snapshots, the rate limiter, and the env fallback of
the proxy source. No network: transports and the API client are injected.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import httpx

from app.databus.x_intel.x_proxy.health import ProxyHealth
from app.databus.x_intel.x_proxy.ratelimit import TokenBucket
from app.databus.x_intel.x_proxy.source import get_proxy_candidates
from app.databus.x_intel.x_proxy.transport import RotatingProxyTransport

if TYPE_CHECKING:
    import pytest

PROXY_A = "http://user:pass@px-a.example:8000"
PROXY_B = "http://user:pass@px-b.example:8000"


class _FakeTransport(httpx.AsyncBaseTransport):
    """Canned per-proxy behaviour: an int status, or an exception to raise."""

    def __init__(self, behaviour: int | Exception) -> None:
        self.behaviour = behaviour
        self.calls = 0
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if isinstance(self.behaviour, Exception):
            raise self.behaviour
        return httpx.Response(status_code=self.behaviour, request=request)

    async def aclose(self) -> None:
        self.closed = True


def _factory(mapping: dict[str | None, int | Exception]):
    """Build a transport factory keyed by proxy URL (None = direct)."""
    made: dict[str | None, _FakeTransport] = {}

    def factory(proxy: str | None) -> httpx.AsyncBaseTransport:
        if proxy not in made:
            made[proxy] = _FakeTransport(mapping.get(proxy, 200))
        return made[proxy]

    return factory, made


def _transport(mapping, *, proxies=(PROXY_A, PROXY_B), health=None, attempts=3):
    factory, made = _factory(mapping)

    async def candidates() -> list[str]:
        return list(proxies)

    transport = RotatingProxyTransport(
        candidates=candidates,
        health=health or ProxyHealth(),
        bucket=TokenBucket(rate=1000, burst=100),
        max_attempts=attempts,
        transport_factory=factory,
    )
    return transport, made


async def _get(transport: RotatingProxyTransport, url: str = "https://x.com/nasa") -> httpx.Response:
    return await transport.handle_async_request(httpx.Request("GET", url))


async def test_rotates_on_403_then_succeeds() -> None:
    transport, made = _transport({PROXY_A: 403, PROXY_B: 200})
    response = await _get(transport)
    assert response.status_code == 200
    assert made[PROXY_A].calls == 1
    assert made[PROXY_B].calls == 1


async def test_rotates_on_429() -> None:
    transport, made = _transport({PROXY_A: 429, PROXY_B: 200})
    assert (await _get(transport)).status_code == 200
    assert made[PROXY_A].calls == 1


async def test_timeout_rotates_to_next_proxy() -> None:
    transport, made = _transport({PROXY_A: httpx.ConnectTimeout("boom"), PROXY_B: 200})
    assert (await _get(transport)).status_code == 200
    assert made[PROXY_B].calls == 1


async def test_all_proxies_blocked_falls_back_direct() -> None:
    transport, made = _transport({PROXY_A: 403, PROXY_B: 429, None: 200})
    response = await _get(transport)
    assert response.status_code == 200
    assert made[None].calls == 1


async def test_every_proxy_down_returns_direct_response() -> None:
    offline = httpx.ConnectError("down")
    transport, made = _transport({PROXY_A: offline, PROXY_B: offline, None: 204})
    response = await _get(transport)
    assert response.status_code == 204
    assert made[None].calls == 1


async def test_circuit_breaker_skips_proxy_tier() -> None:
    health = ProxyHealth(failure_threshold=2, open_seconds=60)
    transport, made = _transport({PROXY_A: 403, PROXY_B: 403, None: 200}, health=health)
    await _get(transport)  # 2 failures -> breaker opens
    assert health.circuit_open() is True
    await _get(transport)  # straight to direct
    assert made[PROXY_A].calls == 1
    assert made[None].calls == 2


async def test_health_snapshot_has_no_credentials() -> None:
    health = ProxyHealth()
    transport, _ = _transport({PROXY_A: 403, PROXY_B: 200}, health=health)
    await _get(transport)
    snapshot = health.snapshot()
    rendered = repr(snapshot)
    assert "pass" not in rendered
    assert "px-a.example" in snapshot["hosts"]
    assert snapshot["hosts"]["px-a.example"]["failure"] == 1
    assert snapshot["hosts"]["px-b.example"]["success"] == 1


async def test_token_bucket_paces_requests() -> None:
    bucket = TokenBucket(rate=50.0, burst=1)
    start = time.monotonic()
    await bucket.acquire()
    await bucket.acquire()
    assert time.monotonic() - start >= 0.005


async def test_source_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBSHARE_API_KEY", raising=False)
    monkeypatch.setenv("WEBSHARE_PROXY_LIST", "px-c.example:9000")
    monkeypatch.setenv("WEBSHARE_PROXY_USER", "u")
    monkeypatch.setenv("WEBSHARE_PROXY_PASS", "p")
    proxies = await get_proxy_candidates(force=True)
    assert proxies == ["http://u:p@px-c.example:9000"]


async def test_source_returns_empty_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBSHARE_API_KEY", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_LIST", raising=False)
    monkeypatch.delenv("WEBSHARE_PROXY_URL", raising=False)
    assert await get_proxy_candidates(force=True) == []


async def test_transport_closes_pools() -> None:
    transport, made = _transport({PROXY_A: 200})
    await _get(transport)
    await transport.aclose()
    assert made[None].closed is True
    assert made[PROXY_A].closed is True
