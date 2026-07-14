"""Integration tests for the scanner endpoint.

Tests that scanner routes are mounted and return correct responses.
The scanner is a stub that returns 501 until the full pipeline is wired.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="module")
def app():
    """Build the FastAPI app once per module."""
    from app.factory import create_app

    return create_app()


class TestScannerEndpoint:
    """Tests for the scanner endpoint (currently a stub)."""

    @pytest.mark.integration
    def test_scan_returns_501(self, app) -> None:
        """POST /scanner/scan should return 501 (not implemented stub)."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/scanner/scan",
            json={"chain": "ethereum", "address": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"},
        )
        assert resp.status_code == 501
        body = resp.json()
        assert "detail" in body
        assert "not yet" in body["detail"].lower() or "not implemented" in body["detail"].lower()

    @pytest.mark.integration
    def test_scan_without_required_field_returns_422(self, app) -> None:
        """POST /scanner/scan without 'address' should return 422."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/scanner/scan", json={"chain": "ethereum"})
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_scan_with_empty_body_returns_422(self, app) -> None:
        """POST /scanner/scan with empty JSON body should return 422."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post("/scanner/scan", json={})
        assert resp.status_code == 422

    @pytest.mark.integration
    def test_scan_result_returns_501(self, app) -> None:
        """GET /scanner/result/{id} should return 501."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/scanner/result/test-scan-id-123")
        assert resp.status_code == 501
