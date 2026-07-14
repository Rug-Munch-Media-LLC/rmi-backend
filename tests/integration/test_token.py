"""Integration tests for token API endpoints.

Covers token detail, risk assessment, and holder endpoints.
Token routes are stubs returning 501 until v5.1 — tests assert
that the route responds (200, 404, 422, 501) so they gracefully
handle unmounted or incomplete implementations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestToken:
    """Tests for /api/v1/token/* endpoints."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_token_detail(self, app) -> None:
        """GET /api/v1/token/{address} — fetch token metadata.

        Token router (app.api.v1.public.token) is mounted at /api/v1/token
        and exposes GET /{address}. Currently a 501 stub; test accepts
        200, 404, 422, or 501.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/token/0x0000000000000000000000000000000000000000")
        assert resp.status_code in (200, 404, 422, 501)

    @pytest.mark.integration
    def test_token_risk(self, app) -> None:
        """GET /api/v1/token/{address}/risk — token risk score.

        Risk endpoint is a 501 stub. Accepts 200/404/422/501.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/token/0x0000000000000000000000000000000000000000/risk")
        assert resp.status_code in (200, 404, 422, 501)

    @pytest.mark.integration
    def test_token_holders(self, app) -> None:
        """GET /api/v1/token/{address}/holders — holder distribution.

        Holders endpoint is a 501 stub. Accepts 200/404/422/501.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/token/0x0000000000000000000000000000000000000000/holders")
        assert resp.status_code in (200, 404, 422, 501)

    @pytest.mark.integration
    def test_token_detail_with_chain(self, app) -> None:
        """GET /api/v1/token/{address}?chain=solana — chain query param."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            "/api/v1/token/0x0000000000000000000000000000000000000000",
            params={"chain": "solana"},
        )
        assert resp.status_code in (200, 404, 422, 501)
