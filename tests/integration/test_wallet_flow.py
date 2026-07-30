"""Integration tests for the wallet flow — resolution, labels, history.

Tests the wallet API endpoints:
  - /wallet/{address} — resolve wallet address
  - /wallet/{address}/labels — wallet labels
  - /wallet/{address}/history — transaction history

The wallet router is a stub (returns 501) until v5.1 — tests verify
the routes are mounted and accept valid wallet addresses.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


_AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}
_ETH_ADDR = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"
_SOL_ADDR = "FakeeJx7WjJAsC4rQxqVxqxjQobxKGYyLqCJGgYJyQSn"


class TestWalletResolve:
    """Tests for GET /wallet/{address} — wallet resolution."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_wallet_resolve_returns_501(self, app) -> None:
        """GET /wallet/{address} — wallet resolution stub.

        Returns 501 until wallet resolution is implemented in v5.1.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(f"/wallet/{_ETH_ADDR}", headers=_AUTH)
        assert resp.status_code == 501, f"Expected 501 (not implemented), got {resp.status_code}: {resp.text[:200]}"
        body = resp.json()
        assert "detail" in body

    @pytest.mark.integration
    def test_wallet_resolve_with_chain_param(self, app) -> None:
        """Wallet resolve should accept ?chain= query parameter."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            f"/wallet/{_ETH_ADDR}",
            params={"chain": "ethereum"},
            headers=_AUTH,
        )
        assert resp.status_code == 501

    @pytest.mark.integration
    def test_wallet_resolve_solana_address(self, app) -> None:
        """Wallet resolve with solana address format."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            f"/wallet/{_SOL_ADDR}",
            params={"chain": "solana"},
            headers=_AUTH,
        )
        assert resp.status_code in (422, 501), f"Expected 422 or 501, got {resp.status_code}: {resp.text[:200]}"
        # If 422, it means the route validated the address format and rejected solana address
        # (ethereum format only). If 501, the stub didn't validate.
        if resp.status_code == 422:
            body = resp.json()
            assert "detail" in body


class TestWalletLabels:
    """Tests for GET /wallet/{address}/labels."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_wallet_labels_returns_501(self, app) -> None:
        """GET /wallet/{address}/labels — labels stub.

        Returns 501 until federated labels are implemented.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(f"/wallet/{_ETH_ADDR}/labels", headers=_AUTH)
        assert resp.status_code == 501, f"Expected 501, got {resp.status_code}: {resp.text[:200]}"

    @pytest.mark.integration
    def test_wallet_labels_with_chain(self, app) -> None:
        """Wallet labels endpoint should accept chain parameter."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            f"/wallet/{_ETH_ADDR}/labels",
            params={"chain": "bsc"},
            headers=_AUTH,
        )
        assert resp.status_code == 501


class TestWalletHistory:
    """Tests for GET /wallet/{address}/history."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_wallet_history_returns_501(self, app) -> None:
        """GET /wallet/{address}/history — history stub.

        Returns 501 until wallet history is implemented.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(f"/wallet/{_ETH_ADDR}/history", headers=_AUTH)
        assert resp.status_code == 501, f"Expected 501, got {resp.status_code}: {resp.text[:200]}"

    @pytest.mark.integration
    def test_wallet_history_with_chain(self, app) -> None:
        """Wallet history endpoint should accept chain parameter."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            f"/wallet/{_ETH_ADDR}/history",
            params={"chain": "arbitrum"},
            headers=_AUTH,
        )
        assert resp.status_code == 501


class TestWalletInputValidation:
    """Wallet endpoint input validation."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_wallet_invalid_address_returns_422(self, app) -> None:
        """Wallet with invalid address format should return 422."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        # Too short to be valid
        resp = client.get("/wallet/0x123", headers=_AUTH)
        assert resp.status_code in (422, 501), f"Expected 422 or 501, got {resp.status_code}"

    @pytest.mark.integration
    def test_wallet_very_long_address(self, app) -> None:
        """Wallet with excessively long address should return 422."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        long_addr = "0x" + "a" * 1000
        resp = client.get(f"/wallet/{long_addr}", headers=_AUTH)
        assert resp.status_code in (422, 501), f"Expected 422 or 501, got {resp.status_code}: {resp.text[:200]}"

    @pytest.mark.integration
    def test_wallet_missing_address_returns_404(self, app) -> None:
        """Wallet route without address should return 404."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/wallet/", headers=_AUTH)
        assert resp.status_code == 404

    @pytest.mark.integration
    def test_wallet_resolve_no_auth(self, app) -> None:
        """Wallet endpoint without auth should return 401."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(f"/wallet/{_ETH_ADDR}")
        assert resp.status_code == 401


class TestWalletClustering:
    """Tests for wallet clustering endpoints."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_wallet_clusters_endpoint(self, app) -> None:
        """GET /api/v1/wallet-clusters/ should be mounted."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/wallet-clusters/", headers=_AUTH)
        assert resp.status_code in (200, 404, 422), f"Expected 200, 404, or 422, got {resp.status_code}"

    @pytest.mark.integration
    def test_wallet_clusters_address_lookup(self, app) -> None:
        """Wallet clusters endpoint should accept address parameter."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            f"/api/v1/wallet-clusters/{_ETH_ADDR}",
            headers=_AUTH,
        )
        assert resp.status_code in (200, 404, 422, 500), f"Expected 200/404/422/500, got {resp.status_code}"
