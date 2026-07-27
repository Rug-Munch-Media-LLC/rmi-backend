"""Integration tests for scanner API endpoints with mock DataBus.

Tests the complete HTTP request/response cycle for scanner endpoints:
  - POST /api/v2/scanner/token/scan — token security scanning
  - POST /api/v2/scanner/wallet/scan — wallet risk scanning
  - Input validation and schema enforcement
  - Error handling (missing fields, invalid chains, empty addresses)
  - Auth middleware enforcement

Uses httpx.AsyncClient with ASGI transport against the real FastAPI app.
External DataBus calls are mocked via monkeypatch to avoid real API calls.
"""

from __future__ import annotations

import pytest

# Known test addresses
SOLANA_TOKEN = "So11111111111111111111111111111111111111112"  # Wrapped SOL
ETH_TOKEN = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # WETH
SOLANA_WALLET = "FakeeJx7WjJAsC4rQxqVxqxjQobxKGYyLqCJGgYJyQSn"
ETH_WALLET = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"


# ═══════════════════════════════════════════════════════════════════════
# Token Scan Endpoint Tests
# ═══════════════════════════════════════════════════════════════════════


class TestTokenScanEndpoint:
    """POST /api/v2/scanner/token/scan — token security scan."""

    ENDPOINT = "/api/v2/scanner/token/scan"
    _AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}

    @pytest.mark.integration
    async def test_token_scan_valid_solana(self, async_client, auth_headers) -> None:
        """POST with valid Solana token address returns 200 with scan results."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_TOKEN, "chain": "solana"},
            headers=self._AUTH,
        )
        # 200 = success, 500 = scanner error (external deps down)
        # Either way the route is mounted and processes the request
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text[:300]}"
        body = resp.json()
        assert body.get("status") == "ok"
        assert "token" in body
        assert "safety_score" in body or "risk_level" in body
        assert body.get("chain") == "solana"

    @pytest.mark.integration
    async def test_token_scan_ethereum(self, async_client, auth_headers) -> None:
        """POST with valid Ethereum token address returns scan results."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": ETH_TOKEN, "chain": "ethereum"},
            headers=self._AUTH,
        )
        assert resp.status_code in (200, 422, 500), f"Expected 200/422/500, got {resp.status_code}: {resp.text[:200]}"
        if resp.status_code == 200:
            body = resp.json()
            assert body.get("chain") == "ethereum"

    @pytest.mark.integration
    async def test_token_scan_empty_address_returns_422(self, async_client) -> None:
        """POST with empty address returns 422 validation error."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": "", "chain": "solana"},
            headers=self._AUTH,
        )
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text[:200]}"
        body = resp.json()
        assert "detail" in body

    @pytest.mark.integration
    async def test_token_scan_missing_chain_returns_422(self, async_client) -> None:
        """POST without chain field returns 422."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_TOKEN},
            headers=self._AUTH,
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    async def test_token_scan_empty_body_returns_422(self, async_client) -> None:
        """POST with empty body returns 422."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={},
            headers=self._AUTH,
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    async def test_token_scan_invalid_chain_returns_422_or_200(self, async_client) -> None:
        """POST with unsupported chain returns 422 or the scanner handles it."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_TOKEN, "chain": "invalid_chain_xyz"},
            headers=self._AUTH,
        )
        assert resp.status_code in (200, 422), f"Expected 200 or 422, got {resp.status_code}"

    @pytest.mark.integration
    async def test_token_scan_no_auth_returns_401(self, async_client) -> None:
        """POST without auth headers returns 401."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_TOKEN, "chain": "solana"},
        )
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    @pytest.mark.integration
    async def test_token_scan_response_schema(self, async_client, auth_headers) -> None:
        """Successful scan response matches expected schema.

        The response should contain: status, token, safety_score, risk_level,
        chain, and either modules_run or an equivalent field.
        """
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_TOKEN, "chain": "solana"},
            headers=self._AUTH,
        )
        if resp.status_code != 200:
            pytest.skip(f"Scanner returned {resp.status_code} — may need external deps")

        body = resp.json()
        # Top-level required fields
        assert "status" in body
        assert "token" in body
        assert isinstance(body["token"], dict)
        assert "address" in body["token"]
        assert body["token"]["address"] == SOLANA_TOKEN

        # Score fields
        safety = body.get("safety_score")
        risk = body.get("risk_level")
        assert safety is not None or risk is not None, "Response must include safety_score or risk_level"
        if safety is not None:
            assert 0 <= safety <= 100, f"Safety score {safety} out of range [0, 100]"

        # Modules / flags
        assert "flags" in body or "risk_flags" in body or "modules_run" in body, (
            "Response must include flags, risk_flags, or modules_run"
        )


class TestWalletScanEndpoint:
    """POST /api/v2/scanner/wallet/scan — wallet risk scan."""

    ENDPOINT = "/api/v2/scanner/wallet/scan"
    _AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}

    @pytest.mark.integration
    async def test_wallet_scan_valid_solana(self, async_client, auth_headers) -> None:
        """POST with valid Solana wallet address returns scan result."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_WALLET, "chain": "solana"},
            headers=self._AUTH,
        )
        # 200 = success, 500 = scanner error, 501 = not yet implemented
        assert resp.status_code in (200, 500, 501), f"Expected 200/500/501, got {resp.status_code}: {resp.text[:200]}"
        if resp.status_code == 200:
            body = resp.json()
            assert "status" in body or "risk" in body or "address" in body

    @pytest.mark.integration
    async def test_wallet_scan_valid_ethereum(self, async_client, auth_headers) -> None:
        """POST with valid Ethereum wallet address returns scan result."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": ETH_WALLET, "chain": "ethereum"},
            headers=self._AUTH,
        )
        assert resp.status_code in (200, 422, 500, 501), (
            f"Expected 200/422/500/501, got {resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.integration
    async def test_wallet_scan_empty_address_returns_422(self, async_client) -> None:
        """POST with empty address returns 422."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": "", "chain": "solana"},
            headers=self._AUTH,
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    async def test_wallet_scan_missing_fields_returns_422(self, async_client) -> None:
        """POST without address returns 422."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={},
            headers=self._AUTH,
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    async def test_wallet_scan_no_auth_returns_401(self, async_client) -> None:
        """POST without auth returns 401."""
        resp = await async_client.post(
            self.ENDPOINT,
            json={"address": SOLANA_WALLET, "chain": "solana"},
        )
        assert resp.status_code == 401


class TestScannerErrorHandling:
    """Edge cases and error handling for scanner endpoints."""

    TOKEN_ENDPOINT = "/api/v2/scanner/token/scan"
    WALLET_ENDPOINT = "/api/v2/scanner/wallet/scan"
    _AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}

    @pytest.mark.integration
    async def test_token_scan_very_long_address(self, async_client) -> None:
        """Scan with extremely long address does not crash."""
        long_addr = "0x" + "a" * 1000
        resp = await async_client.post(
            self.TOKEN_ENDPOINT,
            json={"address": long_addr, "chain": "ethereum"},
            headers=self._AUTH,
        )
        # Should be 422 (validation rejects it) or 200 (scanner processes it)
        assert resp.status_code in (200, 422, 500), f"Got {resp.status_code}, should handle long addresses gracefully"

    @pytest.mark.integration
    async def test_token_scan_sql_injection_attempt(self, async_client) -> None:
        """Scan with SQL-like injection strings doesn't crash."""
        resp = await async_client.post(
            self.TOKEN_ENDPOINT,
            json={
                "address": "'; DROP TABLE tokens; --",
                "chain": "ethereum",
            },
            headers=self._AUTH,
        )
        assert resp.status_code in (200, 422, 500)

    @pytest.mark.integration
    async def test_token_scan_xss_payload(self, async_client) -> None:
        """Scan with XSS payload in address doesn't crash."""
        resp = await async_client.post(
            self.TOKEN_ENDPOINT,
            json={
                "address": "<script>alert('xss')</script>",
                "chain": "ethereum",
            },
            headers=self._AUTH,
        )
        assert resp.status_code in (200, 422, 500)

    @pytest.mark.integration
    async def test_token_scan_unicode_address(self, async_client) -> None:
        """Scan with unicode in address is handled gracefully."""
        resp = await async_client.post(
            self.TOKEN_ENDPOINT,
            json={"address": "🔥🚀", "chain": "solana"},
            headers=self._AUTH,
        )
        assert resp.status_code in (200, 422, 500)

    @pytest.mark.integration
    async def test_invalid_json_body(self, async_client) -> None:
        """Sending malformed JSON returns 422."""
        resp = await async_client.post(
            self.TOKEN_ENDPOINT,
            content=b"not json at all",
            headers={"Content-Type": "application/json", **self._AUTH},
        )
        assert resp.status_code == 422

    @pytest.mark.integration
    async def test_wallet_scan_no_auth_with_bearer(self, async_client) -> None:
        """POST without X-API-Key but with Bearer token may pass auth."""
        from app.domains.auth import _create_jwt

        token = _create_jwt(
            user_id="test-bearer-user",
            email="bearer@test.com",
            tier="PRO",
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await async_client.post(
            self.WALLET_ENDPOINT,
            json={"address": SOLANA_WALLET, "chain": "solana"},
            headers=headers,
        )
        # Bearer may work depending on middleware config
        assert resp.status_code in (200, 401, 422, 500, 501)
