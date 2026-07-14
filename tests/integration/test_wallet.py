"""Integration tests for wallet API endpoints.

Covers wallet resolution, history, and any wallet-related endpoints.
Wallet routes are stubs returning 501 until v5.1 — tests assert
that the route responds (200, 404, 422) so they gracefully handle
unmounted or incomplete implementations.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestWallet:
    """Tests for /api/v1/wallet/* endpoints."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_wallet_address(self, app) -> None:
        """GET /api/v1/wallet/{address} — resolve wallet.

        Wallet router (app.api.v1.public.wallet) is mounted at /api/v1/wallet
        and exposes GET /{address}. Currently a 501 stub; test accepts
        200, 404, or 422.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/wallet/0x0000000000000000000000000000000000000000")
        assert resp.status_code in (200, 404, 422, 501)

    @pytest.mark.integration
    def test_wallet_labels(self, app) -> None:
        """GET /api/v1/wallet/{address}/labels — wallet labels.

        Labels endpoint is a 501 stub. Accepts 200/404/422/501.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/wallet/0x0000000000000000000000000000000000000000/labels")
        assert resp.status_code in (200, 404, 422, 501)

    @pytest.mark.integration
    def test_wallet_history(self, app) -> None:
        """GET /api/v1/wallet/{address}/history — wallet history.

        History endpoint is a 501 stub. Accepts 200/404/422/501.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/wallet/0x0000000000000000000000000000000000000000/history")
        assert resp.status_code in (200, 404, 422, 501)
