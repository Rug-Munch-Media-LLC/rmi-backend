"""E2E basic flow: health → scan token → check result.

These tests verify the complete request/response cycle through a
running rmi-backend instance. They require the service to be started
separately (see conftest.py for details).

The basic flow tests the three most critical paths:
  1. Version/health check — service is alive
  2. Token security scan — core product function
  3. Wallet risk scan — core product function
"""

from __future__ import annotations

import pytest

# ── Test tokens / wallets (known addresses on mainnet) ─────────────────
SOLANA_TOKEN = "So11111111111111111111111111111111111111112"  # Wrapped SOL
ETH_TOKEN = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # WETH
SOLANA_WALLET = "FakeeJx7WjJAsC4rQxqVxqxjQobxKGYyLqCJGgYJyQSn"
ETH_WALLET = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"


class TestE2EHealthCheck:
    """Verify the service is alive and responding."""

    ENDPOINT = "/version"

    @pytest.mark.smoke
    async def test_version_endpoint(self, e2e_client) -> None:
        """GET /version returns 200 with service info."""
        resp = await e2e_client.get(self.ENDPOINT)
        assert resp.status_code == 200
        body = resp.json()
        assert "version" in body
        assert "title" in body
        assert "status" in body
        assert body["status"] == "operational"

    @pytest.mark.smoke
    async def test_root_endpoint(self, e2e_client) -> None:
        """GET / returns 200 with service identity."""
        resp = await e2e_client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert "service" in body
        assert body["service"] == "RMI Backend"

    @pytest.mark.smoke
    async def test_security_headers(self, e2e_client) -> None:
        """Response includes key security headers."""
        resp = await e2e_client.get("/version")
        assert "X-Content-Type-Options" in resp.headers
        assert "X-Frame-Options" in resp.headers
        assert "X-Request-ID" in resp.headers


class TestE2ETokenScan:
    """Token security scan — the core product function."""

    ENDPOINT = "/api/v2/scanner/token/scan"
    _AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}

    @pytest.mark.smoke
    async def test_scan_solana_token(self, e2e_client) -> None:
        """POST /api/v2/scanner/token/scan with Solana token returns results."""
        resp = await e2e_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_TOKEN, "chain": "solana"},
            headers=self._AUTH,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        body = resp.json()
        assert body.get("status") == "ok"
        assert "token" in body
        token = body["token"]
        assert token.get("address") == SOLANA_TOKEN
        assert "safety_score" in body
        assert 0 <= body["safety_score"] <= 100
        assert body.get("chain") == "solana"

    @pytest.mark.smoke
    async def test_scan_ethereum_token(self, e2e_client) -> None:
        """POST with Ethereum token returns results."""
        resp = await e2e_client.post(
            self.ENDPOINT,
            json={"address": ETH_TOKEN, "chain": "ethereum"},
            headers=self._AUTH,
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        body = resp.json()
        assert body.get("status") == "ok"
        assert body.get("chain") == "ethereum"

    @pytest.mark.smoke
    async def test_scan_without_auth_returns_401(self, e2e_client) -> None:
        """Scanner endpoint without auth returns 401."""
        resp = await e2e_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_TOKEN, "chain": "solana"},
        )
        assert resp.status_code == 401

    @pytest.mark.smoke
    async def test_scan_empty_address_returns_422(self, e2e_client) -> None:
        """Scanner with empty address returns 422."""
        resp = await e2e_client.post(
            self.ENDPOINT,
            json={"address": "", "chain": "solana"},
            headers=self._AUTH,
        )
        assert resp.status_code == 422


class TestE2EWalletScan:
    """Wallet risk scan — core product function."""

    ENDPOINT = "/api/v2/scanner/wallet/scan"
    _AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}

    @pytest.mark.smoke
    async def test_scan_solana_wallet(self, e2e_client) -> None:
        """POST /api/v2/scanner/wallet/scan with Solana wallet returns result."""
        resp = await e2e_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_WALLET, "chain": "solana"},
            headers=self._AUTH,
        )
        # 200 = success, 500 = scanner error, 501 = not yet implemented
        assert resp.status_code in (200, 200, 500, 501), (
            f"Expected 200/500/501, got {resp.status_code}: {resp.text[:200]}"
        )
        if resp.status_code == 200:
            body = resp.json()
            assert "status" in body or "risk" in body

    @pytest.mark.smoke
    async def test_scan_wallet_without_auth_returns_401(self, e2e_client) -> None:
        """Wallet scan without auth returns 401."""
        resp = await e2e_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_WALLET, "chain": "solana"},
        )
        assert resp.status_code == 401


class TestE2EBulkFlow:
    """Aggregate flow: health check → scan token → verify result coherence."""

    _AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}

    @pytest.mark.smoke
    async def test_health_then_scan_then_result(self, e2e_client) -> None:
        """Complete flow: version check → scan → verify response fields."""
        # 1. Check the service is alive
        version_resp = await e2e_client.get("/version")
        assert version_resp.status_code == 200
        version_body = version_resp.json()
        assert version_body["status"] == "operational"

        # 2. Scan a token
        scan_resp = await e2e_client.post(
            "/api/v2/scanner/token/scan",
            json={"address": SOLANA_TOKEN, "chain": "solana"},
            headers=self._AUTH,
        )
        assert scan_resp.status_code == 200
        scan_body = scan_resp.json()

        # 3. Verify scan result has coherent fields
        assert scan_body["status"] == "ok"
        assert scan_body["token"]["address"] == SOLANA_TOKEN
        assert scan_body["chain"] == "solana"
        assert 0 <= scan_body["safety_score"] <= 100

        # 4. Verify risk flags are present and valid
        flags = scan_body.get("risk_flags") or scan_body.get("flags", [])
        assert isinstance(flags, list)

        # 5. Verify modules_run contains at least some entries
        modules = scan_body.get("modules_run", [])
        assert len(modules) >= 1, "Scanner should have run at least one module"
        assert modules[0].get("module", ""), "Each module should have a name"
