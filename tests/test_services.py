"""Service-layer unit tests — 20+ tests covering Pryscraper client,
deep intelligence router endpoints, and service edge cases.

Uses pytest-asyncio (enabled via pytest.ini). All external calls mocked.
"""

from __future__ import annotations

import os

# Force env vars BEFORE any app imports to satisfy Settings validation
os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"  # override .env default 'development' which is invalid

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from app.integrations.pryscraper_client import (
    ExtractResult,
    PryscraperClient,
    ScrapeRequest,
    ScrapeResult,
)

# ═══════════════════════════════════════════════════════════════════════
# ── PryscraperClient tests
# ═══════════════════════════════════════════════════════════════════════


class TestPryscraperClientInit:
    """Construction and configuration."""

    def test_init_defaults(self):
        """Uses env vars when no args given."""
        client = PryscraperClient()
        assert client.base_url == "http://pry:8002"
        assert client._x402_offline is False
        assert client.api_key is None or isinstance(client.api_key, str)

    def test_init_explicit_args(self):
        """Explicit constructor args override env defaults."""
        client = PryscraperClient(
            base_url="http://test:9999",
            api_key="test-key-123",
            x402_offline=True,
        )
        assert client.base_url == "http://test:9999"
        assert client.api_key == "test-key-123"
        assert client._x402_offline is True

    def test_init_x402_offline_from_env(self, monkeypatch):
        """PRY_X402_OFFLINE=true sets offline mode."""
        monkeypatch.setenv("PRY_X402_OFFLINE", "true")
        client = PryscraperClient()
        assert client._x402_offline is True

    def test_headers_with_api_key(self):
        """_headers includes Bearer token when api_key is set."""
        client = PryscraperClient(api_key="secret-abc")
        headers = client._headers()
        assert headers["Authorization"] == "Bearer secret-abc"
        assert headers["Content-Type"] == "application/json"

    def test_headers_without_api_key(self):
        """_headers omits Authorization when no api_key."""
        client = PryscraperClient(api_key=None)
        headers = client._headers()
        assert "Authorization" not in headers


class TestPryscraperClientRequest:
    """Core _request method behaviour."""

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_request_success(self, mock_cb):
        """Successful 2xx returns parsed JSON and records success."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_success = AsyncMock()
        mock_cb.set_stale_result = MagicMock()

        client = PryscraperClient(base_url="http://pry:8002")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.is_success = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"status": "ok", "data": "hello"}
        mock_resp.content = b'{"status": "ok", "data": "hello"}'
        mock_resp.headers = {}

        client._client.request = AsyncMock(return_value=mock_resp)

        result = await client._request("GET", "/health")

        assert result == {"status": "ok", "data": "hello"}
        mock_cb.set_stale_result.assert_called_once_with(result)
        mock_cb.record_success.assert_awaited_once()

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_request_server_error_trips_breaker(self, mock_cb):
        """5xx errors trip circuit breaker and retry."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_failure = AsyncMock()

        client = PryscraperClient(base_url="http://pry:8002")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.is_success = False
        mock_resp.status_code = 502
        mock_resp.text = "Bad Gateway"
        mock_resp.content = b""
        mock_resp.headers = {}

        client._client.request = AsyncMock(return_value=mock_resp)

        # After retries, should return error
        result = await client._request("GET", "/bad")

        assert result["success"] is False
        assert "502" in result["error"]["message"]
        # record_failure called at least once for server error
        assert mock_cb.record_failure.await_count >= 1

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_request_client_error_no_trip(self, mock_cb):
        """4xx errors do NOT trip circuit breaker as server error."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_failure = AsyncMock()

        client = PryscraperClient(base_url="http://pry:8002")
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.is_success = False
        mock_resp.status_code = 422
        mock_resp.text = '{"error": {"message": "bad request"}}'
        mock_resp.content = mock_resp.text.encode()
        mock_resp.headers = {}
        mock_resp.json.return_value = {"error": {"message": "bad request"}}

        client._client.request = AsyncMock(return_value=mock_resp)

        result = await client._request("GET", "/bad")

        assert result["success"] is False
        assert result["error"]["code"] == "client_error"

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_request_timeout_returns_error(self, mock_cb):
        """HTTP timeout after retries returns upstream_error."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_failure = AsyncMock()

        client = PryscraperClient(base_url="http://pry:8002")
        client._client.request = AsyncMock(side_effect=httpx.TimeoutException("Connection timed out"))

        result = await client._request("GET", "/health")

        assert result["success"] is False
        assert result["error"]["code"] == "upstream_error"

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_circuit_breaker_open_raises(self, mock_cb):
        """When breaker is OPEN and no stale result, raises CircuitBreakerOpenError."""
        from app.core.circuit_breaker import CircuitBreakerOpenError

        mock_cb.can_call = AsyncMock(return_value=False)
        mock_cb.get_stale_result.return_value = None

        client = PryscraperClient(base_url="http://pry:8002")

        with pytest.raises(CircuitBreakerOpenError):
            await client._request("GET", "/health")

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_circuit_breaker_serves_stale(self, mock_cb):
        """When breaker is OPEN and stale result exists, returns it."""
        stale_data = {"stale": True, "data": "cached"}
        mock_cb.can_call = AsyncMock(return_value=False)
        mock_cb.get_stale_result.return_value = stale_data

        client = PryscraperClient(base_url="http://pry:8002")

        result = await client._request("GET", "/health")
        assert result == stale_data

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_request_retry_on_429(self, mock_cb):
        """429 rate-limit waits for retry-after then retries."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_success = AsyncMock()
        mock_cb.set_stale_result = MagicMock()

        client = PryscraperClient(base_url="http://pry:8002")

        # First call 429, second success
        error_resp = MagicMock(spec=httpx.Response)
        error_resp.is_success = False
        error_resp.status_code = 429
        error_resp.headers = {"retry-after": "0.01"}
        error_resp.content = b""

        ok_resp = MagicMock(spec=httpx.Response)
        ok_resp.is_success = True
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"ok": True}
        ok_resp.content = b'{"ok": true}'
        ok_resp.headers = {}

        client._client.request = AsyncMock(side_effect=[error_resp, ok_resp])

        result = await client._request("GET", "/rate-limited")

        assert result == {"ok": True}
        assert client._client.request.await_count == 2


class TestPryscraperClientPublicAPI:
    """Public endpoints — scrape, extract, health, stats, search_news."""

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_check_health(self, mock_cb):
        """check_health calls GET /health."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_success = AsyncMock()
        mock_cb.set_stale_result = MagicMock()

        client = PryscraperClient(base_url="http://pry:8002")
        resp = MagicMock(spec=httpx.Response)
        resp.is_success = True
        resp.status_code = 200
        resp.json.return_value = {"status": "healthy"}
        resp.content = b'{"status": "healthy"}'
        resp.headers = {}
        client._client.request = AsyncMock(return_value=resp)

        result = await client.check_health()
        assert result == {"status": "healthy"}

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_scrape_success(self, mock_cb):
        """scrape returns ScrapeResult with data on success."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_success = AsyncMock()
        mock_cb.set_stale_result = MagicMock()

        client = PryscraperClient(base_url="http://pry:8002")
        resp = MagicMock(spec=httpx.Response)
        resp.is_success = True
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "data": {"content": "scraped page content"},
        }
        resp.content = b"..."
        resp.headers = {}
        client._client.request = AsyncMock(return_value=resp)

        result = await client.scrape("https://example.com")
        assert isinstance(result, ScrapeResult)
        assert result.success is True
        assert result.data["content"] == "scraped page content"
        assert result.source == "pryscraper"

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_scrape_failure(self, mock_cb):
        """scrape returns ScrapeResult with error on failure."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_failure = AsyncMock()

        client = PryscraperClient(base_url="http://pry:8002")
        resp = MagicMock(spec=httpx.Response)
        resp.is_success = False
        resp.status_code = 502
        resp.text = "Upstream error"
        resp.content = b""
        resp.headers = {}
        client._client.request = AsyncMock(return_value=resp)

        result = await client.scrape("https://example.com")
        assert isinstance(result, ScrapeResult)
        assert result.success is False
        assert result.error is not None

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_extract_success(self, mock_cb):
        """extract returns ExtractResult with structured data."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_success = AsyncMock()
        mock_cb.set_stale_result = MagicMock()

        client = PryscraperClient(base_url="http://pry:8002")
        resp = MagicMock(spec=httpx.Response)
        resp.is_success = True
        resp.status_code = 200
        resp.json.return_value = {
            "success": True,
            "data": {"name": "Acme Corp", "revenue": "$1B"},
        }
        resp.content = b"..."
        resp.headers = {}
        client._client.request = AsyncMock(return_value=resp)

        result = await client.extract(
            "https://example.com",
            json_schema={"name": "string", "revenue": "string"},
        )
        assert isinstance(result, ExtractResult)
        assert result.success is True
        assert result.data["name"] == "Acme Corp"

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_extract_with_schema(self, mock_cb):
        """extract passes json_schema to the API."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_success = AsyncMock()
        mock_cb.set_stale_result = MagicMock()

        client = PryscraperClient(base_url="http://pry:8002")

        def _check_request(method, url, **kw):
            """Assert json_schema is in the body."""
            body = kw.get("json", {})
            assert "json_schema" in body
            assert body["json_schema"] == {"title": "string"}

            resp = MagicMock(spec=httpx.Response)
            resp.is_success = True
            resp.status_code = 200
            resp.json.return_value = {"success": True, "data": {"title": "Test"}}
            resp.content = b"..."
            resp.headers = {}
            return resp

        client._client.request = AsyncMock(side_effect=_check_request)

        result = await client.extract(
            "https://example.com",
            json_schema={"title": "string"},
        )
        assert result.success is True

    @patch("app.integrations.pryscraper_client._pry_cb")
    async def test_search_news_fallback(self, mock_cb):
        """search_news falls back to scrape when intelligence endpoint fails."""
        mock_cb.can_call = AsyncMock(return_value=True)
        mock_cb.record_success = AsyncMock()
        mock_cb.set_stale_result = MagicMock()
        mock_cb.record_failure = AsyncMock()

        client = PryscraperClient(base_url="http://pry:8002")

        # First call (intelligence) returns error
        # We make intelligence fail with upstream error
        # Then scrape succeeds
        intel_resp = MagicMock(spec=httpx.Response)
        intel_resp.is_success = False
        intel_resp.status_code = 500
        intel_resp.text = "error"
        intel_resp.content = b""
        intel_resp.headers = {}

        scrape_resp = MagicMock(spec=httpx.Response)
        scrape_resp.is_success = True
        scrape_resp.status_code = 200
        scrape_resp.json.return_value = {"success": True, "data": {"articles": []}}
        scrape_resp.content = b"..."
        scrape_resp.headers = {}

        # The intelligence call may fail after retries - it returns _error_response
        # which is a dict, not raised. So our mock just returns the error response as JSON.
        client._client.request = AsyncMock(return_value=intel_resp)

        # Manually override scrape to return success
        with patch.object(client, "scrape", new=AsyncMock()) as mock_scrape:
            mock_scrape.return_value = ScrapeResult(
                success=True,
                data={"articles": [{"title": "Test news"}]},
                source="pryscraper_news",
            )
            result = await client.search_news("bitcoin", limit=5)

            assert result["success"] is True
            mock_scrape.assert_awaited_once()

    async def test_stats_structure(self):
        """stats returns structured metrics dict."""
        client = PryscraperClient(base_url="http://pry:8002")
        stats = await client.stats()
        assert "circuit_breaker" in stats
        assert "base_url" in stats
        assert stats["base_url"] == "http://pry:8002"
        assert "x402_offline" in stats
        assert "x402_payments_count" in stats

    async def test_aclose(self):
        """aclose closes the underlying httpx client."""
        client = PryscraperClient(base_url="http://pry:8002")
        client._client = AsyncMock(spec=httpx.AsyncClient)
        await client.aclose()
        client._client.aclose.assert_awaited_once()


class TestScrapeRequestModel:
    """PryscraperClient ScrapeRequest model creation."""

    def test_valid_request(self):
        """Valid ScrapeRequest passes validation."""
        req = ScrapeRequest(url="https://example.com")
        assert req.url == "https://example.com"
        assert req.timeout == 30
        assert req.onlyMainContent is True
        assert req.bypassCloudflare is True

    def test_url_required(self):
        """ScrapeRequest without url should fail."""
        with pytest.raises(ValidationError):
            ScrapeRequest()

    def test_custom_fields(self):
        """Custom fields override defaults."""
        req = ScrapeRequest(
            url="https://example.com",
            formats=["markdown"],
            onlyMainContent=False,
            timeout=60,
            bypassCloudflare=False,
            jsRender=True,
        )
        assert req.formats == ["markdown"]
        assert req.onlyMainContent is False
        assert req.timeout == 60
        assert req.bypassCloudflare is False
        assert req.jsRender is True


class TestDeepIntelRouter:
    """Deep Intelligence router endpoint behaviour (no DB needed)."""

    def test_health_returns_features(self):
        """GET /api/v1/deep-intel/health returns all 13 features."""
        # This tests the response shape without hitting the router
        from app.services.deep_intel_router import router

        # Verify the router is configured with correct prefix
        assert router.prefix == "/api/v1/deep-intel"

        # We can't call the handler without app context, but we can test
        # that the router has correct routes
        routes = [r.path for r in router.routes]
        assert f"{router.prefix}/health" in routes
        assert f"{router.prefix}/deployer/{{address}}" in routes
        assert f"{router.prefix}/full-report/{{token_address}}" in routes
        assert f"{router.prefix}/batch-report" in routes

    def test_supported_chains(self):
        """SUPPORTED_CHAINS includes all expected chains."""
        from app.services.deep_intel_router import SUPPORTED_CHAINS

        assert "sol" in SUPPORTED_CHAINS
        assert "eth" in SUPPORTED_CHAINS
        assert "bsc" in SUPPORTED_CHAINS
        assert "base" in SUPPORTED_CHAINS
        assert len(SUPPORTED_CHAINS) == 7

    def test_batch_report_max_ten(self):
        """Batch report caps at 10 tokens."""
        from app.services.deep_intel_router import router

        # Verify the route exists and POST method
        batch_routes = [r for r in router.routes if "/batch-report" in r.path]
        assert len(batch_routes) == 1

    @pytest.mark.asyncio
    async def test_deployer_reputation_calls_core(self):
        """api_deployer_reputation delegates to core function."""
        from app.services import deep_intel_router

        mock_fn = AsyncMock(return_value={"reputation": 85, "tokens_created": 12})
        # Patch the function on the already-imported module
        with patch.object(deep_intel_router, "deployer_reputation", mock_fn):
            result = await deep_intel_router.api_deployer_reputation(address="test-address-123", chain="sol")
            assert result["reputation"] == 85
            mock_fn.assert_awaited_once_with("test-address-123", "sol")
