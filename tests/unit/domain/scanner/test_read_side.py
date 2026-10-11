"""Tests for the standalone scanner service client (read_side)."""

from __future__ import annotations

import os

# Force env vars BEFORE any app imports
os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.domains.scanner.read_side import fetch_read_side


@pytest.fixture
def mock_settings():
    """Mock settings with scanner service URL."""
    with patch("app.domains.scanner.read_side.settings") as mock:
        yield mock


class TestFetchReadSide:
    """Tests for fetch_read_side function."""

    @pytest.mark.asyncio
    async def test_disabled_when_url_empty(self, mock_settings):
        """Should return None when scanner_service_url is empty."""
        mock_settings.scanner_service_url = ""
        result = await fetch_read_side("0x123", "ethereum")
        assert result is None

    @pytest.mark.asyncio
    async def test_success_case(self, mock_settings):
        """Should return trimmed data on successful 200 OK response."""
        mock_settings.scanner_service_url = "http://test-service:8098"
        mock_response_data = {
            "status": "ok",
            "risk_score": 75,
            "risk_level": "high",
            "signals": ["honeypot_detected"],
            "corroboration": "multiple_sources",
            "confidence_tier": "B",
            "coverage": "comprehensive",
            "duration_ms": 150,
            "extra_field": "should_be_trimmed",
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_response_data)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.scanner.read_side.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_read_side("0x123", "ethereum")

            assert result is not None
            assert result["status"] == "ok"
            assert result["risk_score"] == 75
            assert result["risk_level"] == "high"
            assert result["signals"] == ["honeypot_detected"]
            assert result["corroboration"] == "multiple_sources"
            assert result["confidence_tier"] == "B"
            assert result["coverage"] == "comprehensive"
            assert result["duration_ms"] == 150
            # Extra field should be trimmed
            assert "extra_field" not in result

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self, mock_settings):
        """Should return None on timeout."""
        mock_settings.scanner_service_url = "http://test-service:8098"
        mock_client = AsyncMock()
        # Make post raise TimeoutException
        mock_client.post.side_effect = httpx.TimeoutException("timeout")
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.scanner.read_side.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_read_side("0x123", "ethereum")

            assert result is None

    @pytest.mark.asyncio
    async def test_connection_error_returns_none(self, mock_settings):
        """Should return None on connection error."""
        mock_settings.scanner_service_url = "http://test-service:8098"
        mock_client = AsyncMock()
        # Make post raise ConnectError
        mock_client.post.side_effect = httpx.ConnectError("connection failed")
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.scanner.read_side.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_read_side("0x123", "ethereum")

            assert result is None

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_none(self, mock_settings):
        """Should return None on unexpected error."""
        mock_settings.scanner_service_url = "http://test-service:8098"
        mock_client = AsyncMock()
        # Make post raise generic Exception
        mock_client.post.side_effect = Exception("unexpected")
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.scanner.read_side.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_read_side("0x123", "ethereum")

            assert result is None

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self, mock_settings):
        """Should return None on non-200 status code."""
        mock_settings.scanner_service_url = "http://test-service:8098"
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.scanner.read_side.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_read_side("0x123", "ethereum")

            assert result is None

    @pytest.mark.asyncio
    async def test_invalid_json_returns_none(self, mock_settings):
        """Should return None on invalid JSON response."""
        mock_settings.scanner_service_url = "http://test-service:8098"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = Exception("invalid json")

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.scanner.read_side.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_read_side("0x123", "ethereum")

            assert result is None

    @pytest.mark.asyncio
    async def test_status_not_ok_returns_none(self, mock_settings):
        """Should return None when status is not 'ok'."""
        mock_settings.scanner_service_url = "http://test-service:8098"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "error", "message": "something went wrong"}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.scanner.read_side.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_read_side("0x123", "ethereum")

            assert result is None

    @pytest.mark.asyncio
    async def test_partial_data_still_works(self, mock_settings):
        """Should work even if some optional fields are missing."""
        mock_settings.scanner_service_url = "http://test-service:8098"
        mock_response_data = {
            "status": "ok",
            "risk_score": 50,
            # risk_level missing
            "signals": [],
            # corroboration missing
            "confidence_tier": "C",
            # coverage missing
            "duration_ms": 100,
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=mock_response_data)

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__.return_value = mock_client

        with patch("app.domains.scanner.read_side.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_read_side("0x123", "ethereum")

            assert result is not None
            assert result["status"] == "ok"
            assert result["risk_score"] == 50
            assert result["risk_level"] is None  # missing -> None
            assert result["signals"] == []
            assert result["corroboration"] is None  # missing -> None
            assert result["confidence_tier"] == "C"
            assert result["coverage"] is None  # missing -> None
            assert result["duration_ms"] == 100
