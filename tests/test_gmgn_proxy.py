"""Proxy + API-key wiring tests for :mod:`app.databus.gmgn_provider`.

Pure unit tests — the ProxyRotator and both httpx clients are mocked, so no
network, no Redis, no DB. Verifies:

  * ``X-APIKEY`` header is attached when ``GMGN_API_KEY`` is set (and absent
    otherwise) — the auth convention used by the official GMGN SDK.
  * The provider builds its proxied client via ``get_proxy_rotator()`` when
    the rotator is enabled, and falls back to a direct client when disabled.
  * A proxy 403 (Cloudflare IP block) triggers ``mark_failed`` + a single
    direct retry; a proxy 200 short-circuits without a direct call.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.databus import gmgn_provider as gmgn_mod


@pytest.fixture
def reload_gmgn(monkeypatch):
    """Reload the module after (un)setting GMGN_API_KEY so module constants refresh.

    The module reads ``GMGN_API_KEY`` at import time, so tests must reload to
    observe env changes. After each test the original env is restored and the
    module is reloaded again so later tests start clean.
    """
    orig = gmgn_mod.GMGN_API_KEY

    def _apply(api_key: str | None):
        if api_key is None:
            monkeypatch.delenv("GMGN_API_KEY", raising=False)
        else:
            monkeypatch.setenv("GMGN_API_KEY", api_key)
        return importlib.reload(gmgn_mod)

    yield _apply

    if orig:
        os.environ["GMGN_API_KEY"] = orig
    else:
        os.environ.pop("GMGN_API_KEY", None)
    importlib.reload(gmgn_mod)


def _proxy_200_payload() -> dict:
    """GMGN Agent API shape (2026-08-23): price is a nested object with
    per-window stats; supplies are token-level fields."""
    return {
        "data": {
            "token": {
                "address": "TOKEN123",
                "symbol": "PUMP",
                "name": "Pump Token",
                "price": {
                    "price": 0.00123,
                    "price_1h": 0.5,
                    "price_24h": -2.0,
                    "volume_24h": 9999.0,
                    "buys_24h": 100,
                    "sells_24h": 80,
                },
                "circulating_supply": 123000000,
                "total_supply": 500000000,
                "liquidity": 8000,
                "holder_count": 250,
                "creation_timestamp": 1723000000,
                "launchpad": "pump",
            }
        }
    }


def _make_provider(proxy_client=None, rotator_enabled: bool = True):
    """Build a GMGNProvider with mocked rotator + direct httpx client."""
    rotator = MagicMock()
    rotator.enabled = rotator_enabled
    rotator.get_rotating_proxy.return_value = "http://u:p@proxy1.example.com:8080"
    rotator.next_after_failure.return_value = "http://u:p@proxy2.example.com:8080"
    rotator.get_client.return_value = proxy_client
    direct_client = AsyncMock()
    with (
        patch("app.databus.gmgn_provider.get_proxy_rotator", return_value=rotator),
        patch("app.databus.gmgn_provider.httpx.AsyncClient", return_value=direct_client),
    ):
        provider = gmgn_mod.GMGNProvider()
    return provider, rotator, direct_client


class TestApiKeyHeader:
    def test_api_key_attached_when_set(self, reload_gmgn):
        reload_gmgn("test-key-123")
        provider, _rotator, _direct = _make_provider()
        assert provider.headers["X-APIKEY"] == "test-key-123"
        assert "Authorization" not in provider.headers

    def test_no_api_key_header_when_unset(self, reload_gmgn):
        reload_gmgn(None)
        provider, _rotator, _direct = _make_provider()
        assert "X-APIKEY" not in provider.headers


class TestClientWiring:
    async def test_proxy_client_used_when_enabled(self):
        proxy_client = AsyncMock()
        provider, rotator, direct_client = _make_provider(proxy_client, rotator_enabled=True)
        assert provider._proxy_client is proxy_client
        assert provider._proxy_url == "http://u:p@proxy1.example.com:8080"
        rotator.get_rotating_proxy.assert_called_once()
        rotator.get_client.assert_called_once_with(proxy="http://u:p@proxy1.example.com:8080")
        assert provider.client is direct_client

    async def test_direct_client_used_when_disabled(self):
        provider, rotator, direct_client = _make_provider(None, rotator_enabled=False)
        assert provider._proxy_client is None
        assert provider._proxy_url is None
        rotator.get_rotating_proxy.assert_not_called()
        rotator.get_client.assert_not_called()
        assert provider.client is direct_client


class TestProxyFailureRetry:
    async def test_403_triggers_mark_failed_and_direct_retry(self, reload_gmgn):
        # Hermetic module state: without the reload this test inherited
        # whatever GMGN_API_KEY state a previous test in the worker left
        # (order-dependent under xdist; failed in CI, passed in isolation).
        reload_gmgn("test-key-123")
        proxy_client = AsyncMock()
        resp_403 = MagicMock()
        resp_403.status_code = 403
        proxy_client.request.return_value = resp_403

        provider, rotator, direct_client = _make_provider(proxy_client, rotator_enabled=True)

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = _proxy_200_payload()
        direct_client.request.return_value = resp_200

        result = await provider._fetch_token_info("TOKEN123", "solana")

        assert result is not None
        assert result["symbol"] == "PUMP"
        assert result["source"] == "gmgn"
        rotator.mark_failed.assert_called_once_with("http://u:p@proxy1.example.com:8080")
        rotator.next_after_failure.assert_called_once()
        proxy_client.request.assert_called_once()
        direct_client.request.assert_called_once()

    async def test_proxy_timeout_triggers_mark_failed_and_direct_retry(self):
        proxy_client = AsyncMock()
        proxy_client.request.side_effect = httpx.ConnectError("proxy timeout")

        provider, rotator, direct_client = _make_provider(proxy_client, rotator_enabled=True)

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = _proxy_200_payload()
        direct_client.request.return_value = resp_200

        result = await provider._fetch_token_info("TOKEN123", "solana")

        assert result is not None
        rotator.mark_failed.assert_called_once_with("http://u:p@proxy1.example.com:8080")
        direct_client.request.assert_called_once()

    async def test_proxy_200_short_circuits_without_direct_call(self):
        proxy_client = AsyncMock()
        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.json.return_value = _proxy_200_payload()
        proxy_client.request.return_value = resp_200

        provider, rotator, direct_client = _make_provider(proxy_client, rotator_enabled=True)

        result = await provider._fetch_token_info("TOKEN123", "solana")

        assert result is not None
        rotator.mark_failed.assert_not_called()
        direct_client.request.assert_not_called()

    async def test_direct_403_returns_none_without_mark_failed(self):
        # No proxy configured: a direct 403 is simply a non-200 response.
        provider, rotator, direct_client = _make_provider(None, rotator_enabled=False)
        resp_403 = MagicMock()
        resp_403.status_code = 403
        direct_client.request.return_value = resp_403

        result = await provider._fetch_token_info("TOKEN123", "solana")

        assert result is None
        rotator.mark_failed.assert_not_called()
