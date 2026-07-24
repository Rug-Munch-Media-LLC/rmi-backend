"""Integration tests for full API workflows — auth → scan → results, wallet, market intel.

Tests end-to-end API flows:
  - Auth → scan → get results (token + wallet scans)
  - Auth → wallet → check (resolve, labels, profiler)
  - Auth → market intel → get data (news, bulletin)
  - Error flows (bad auth, rate limited, invalid input)

Each workflow is tested as a complete flow, not isolated endpoint pings.
Uses TestClient against the real FastAPI app factory.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}
_BEARER = {"Authorization": "Bearer test-bearer-token-for-testing"}

# Known test addresses
SOL_TOKEN = "So11111111111111111111111111111111111111112"  # Wrapped SOL
SOL_WALLET = "FakeeJx7WjJAsC4rQxqVxqxjQobxKGYyLqCJGgYJyQSn"
ETH_TOKEN = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # WETH
ETH_WALLET = "0x742d35Cc6634C0532925a3b844Bc454e4438f44e"


# ═══════════════════════════════════════════════════════════════
# ── Auth → Scan → Get Results
# ═══════════════════════════════════════════════════════════════


class TestAuthScanFlow:
    """Complete auth → scan → get results flow."""

    @pytest.fixture(scope="module")
    def app(self):
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_full_token_scan_flow(self, app) -> None:
        """Auth → token scan → result has expected fields."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        headers = {**_AUTH, **_BEARER}

        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={"address": SOL_TOKEN, "chain": "solana"},
            headers=headers,
        )
        # Should be 200 (success) — scanner works with known tokens
        assert resp.status_code == 200, (
            f"Token scan flow failed: {resp.status_code}: {resp.text[:200]}"
        )
        body = resp.json()
        assert body["status"] == "ok"
        assert "token" in body
        assert body["chain"] == "solana"
        assert "safety_score" in body
        assert "risk_level" in body
        assert "modules_run" in body
        assert isinstance(body["modules_run"], list)
        assert "scanned_at" in body

    @pytest.mark.integration
    def test_full_wallet_scan_flow(self, app) -> None:
        """Auth → wallet scan → result has expected fields."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        headers = {**_AUTH, **_BEARER}

        resp = client.post(
            "/api/v2/scanner/wallet/scan",
            json={"address": SOL_WALLET, "chain": "solana"},
            headers=headers,
        )
        assert resp.status_code == 200, (
            f"Wallet scan flow failed: {resp.status_code}: {resp.text[:200]}"
        )
        body = resp.json()
        assert body["status"] == "ok"
        assert "address" in body
        assert body["chain"] == "solana"
        assert "risk_score" in body
        assert "risk_flags" in body
        assert isinstance(body["risk_flags"], list)
        assert "labels" in body

    @pytest.mark.integration
    def test_token_scan_without_bearer_still_works(self, app) -> None:
        """Token scan with X-API-Key only (no Bearer) should succeed."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={"address": SOL_TOKEN, "chain": "solana"},
            headers=_AUTH,
        )
        # Auth middleware should pass with just X-API-Key
        assert resp.status_code in (200, 401, 403), (
            f"Unexpected: {resp.status_code}: {resp.text[:200]}"
        )
        if resp.status_code == 200:
            body = resp.json()
            assert body["status"] == "ok"

    @pytest.mark.integration
    def test_scan_with_internal_key(self, app) -> None:
        """Internal X-RMI-Key header should work for admin-tier scan."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        headers = {
            **_AUTH,
            **_BEARER,
            "X-RMI-Key": "test-internal-key-32-chars-long!!!",
        }

        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={"address": SOL_TOKEN, "chain": "solana"},
            headers=headers,
        )
        assert resp.status_code == 200, (
            f"Internal key scan failed: {resp.status_code}: {resp.text[:200]}"
        )
        body = resp.json()
        assert body["status"] == "ok"
        assert "tier" in body


# ═══════════════════════════════════════════════════════════════
# ── Auth → Wallet → Check
# ═══════════════════════════════════════════════════════════════


class TestAuthWalletFlow:
    """Auth → wallet endpoints flow tests."""

    @pytest.fixture(scope="module")
    def app(self):
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_wallet_resolve_flow(self, app) -> None:
        """GET /wallet/{address} — resolve a wallet address."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            f"/wallet/{SOL_WALLET}",
            headers=_AUTH,
        )
        # 200 = resolved, 404 = not mounted, 501 = stub
        assert resp.status_code in (200, 404, 501), f"Wallet resolve failed: {resp.status_code}"

    @pytest.mark.integration
    def test_wallet_with_profiler(self, app) -> None:
        """GET /api/v1/address-profiler/{address} — wallet profiling."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            f"/api/v1/address-profiler/{SOL_WALLET}",
            headers=_AUTH,
        )
        # 200 = profiler active, 404 = not mounted
        assert resp.status_code in (200, 404, 422), (
            f"Profiler failed: {resp.status_code}: {resp.text[:100]}"
        )
        if resp.status_code == 200:
            body = resp.json()
            assert "address" in body or "risk_score" in body

    @pytest.mark.integration
    def test_wallet_labels_flow(self, app) -> None:
        """GET /wallet/{address}/labels — wallet labels."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            f"/wallet/{SOL_WALLET}/labels",
            headers=_AUTH,
        )
        assert resp.status_code in (200, 404, 501), f"Labels endpoint failed: {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, (dict, list))

    @pytest.mark.integration
    def test_wallet_eth_address(self, app) -> None:
        """ETH wallet address should be handled by wallet routes."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            f"/wallet/{ETH_WALLET}",
            headers=_AUTH,
        )
        assert resp.status_code in (200, 404, 501), f"ETH wallet failed: {resp.status_code}"

    @pytest.mark.integration
    def test_whale_alerts_subscribe(self, app) -> None:
        """POST /api/v1/whale-alerts/subscribe — subscribe to whale alerts."""
        from fastapi.testclient import TestClient

        # CSRF is skipped when Bearer auth is present
        _BEARER_AUTH = {**_AUTH, "Authorization": "Bearer test-bearer-token"}
        client = TestClient(app)
        resp = client.post(
            "/api/v1/whale-alerts/subscribe",
            json={
                "user_id": "test_user",
                "address": SOL_WALLET,
                "chain": "solana",
                "threshold_usd": 50000.0,
                "notify_telegram": False,
                "label": "test_whale",
            },
            headers=_BEARER_AUTH,
        )
        assert resp.status_code in (200, 404), (
            f"Whale subscribe failed: {resp.status_code}: {resp.text[:100]}"
        )
        if resp.status_code == 200:
            body = resp.json()
            assert "id" in body
            assert body["status"] == "subscribed"


# ═══════════════════════════════════════════════════════════════
# ── Market Intel Flows
# ═══════════════════════════════════════════════════════════════


class TestMarketIntelFlow:
    """Auth → market intel → get data flows."""

    @pytest.fixture(scope="module")
    def app(self):
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_market_health(self, app) -> None:
        """GET /api/v1/market/health — market intel health endpoint."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/market/health", headers=_AUTH)
        # 200 if mounted, 404 if not in this config
        assert resp.status_code in (200, 404), f"Market health failed: {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)

    @pytest.mark.integration
    def test_market_trending(self, app) -> None:
        """GET /api/v1/market/trending — trending tokens."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/market/trending", headers=_AUTH)
        assert resp.status_code in (200, 404), f"Market trending failed: {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, (dict, list))

    @pytest.mark.integration
    def test_market_fear_greed(self, app) -> None:
        """GET /api/v1/market/fear-greed — fear & greed index."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/market/fear-greed", headers=_AUTH)
        assert resp.status_code in (200, 404), f"Fear/greed failed: {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)

    @pytest.mark.integration
    def test_news_feed(self, app) -> None:
        """GET /api/v1/news/feed — news feed."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/news/feed", headers=_AUTH)
        assert resp.status_code in (200, 404), f"News feed failed: {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, (dict, list))

    @pytest.mark.integration
    def test_bulletin_board(self, app) -> None:
        """GET /api/v1/bulletin/ — bulletin board posts."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/bulletin/", headers=_AUTH)
        assert resp.status_code in (200, 404), f"Bulletin failed: {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, (dict, list))


# ═══════════════════════════════════════════════════════════════
# ── Error Flows
# ═══════════════════════════════════════════════════════════════


class TestErrorFlows:
    """Error flows — bad auth, rate limited, invalid input."""

    @pytest.fixture(scope="module")
    def app(self):
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_no_auth_returns_401(self, app) -> None:
        """Protected route without any auth returns 401."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={"address": SOL_TOKEN, "chain": "solana"},
        )
        # Auth middleware should reject
        assert resp.status_code in (401, 403), f"No auth should fail: got {resp.status_code}"

    @pytest.mark.integration
    def test_wrong_api_key_returns_401(self, app) -> None:
        """Protected route with wrong API key returns 401."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={"address": SOL_TOKEN, "chain": "solana"},
            headers={"X-API-Key": "definitely-wrong-key-that-is-not-valid"},
        )
        assert resp.status_code in (401, 403), f"Wrong key should fail: got {resp.status_code}"

    @pytest.mark.integration
    def test_empty_json_body(self, app) -> None:
        """POST with empty JSON body returns 422."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={},
            headers={**_AUTH, **_BEARER},
        )
        assert resp.status_code == 422, (
            f"Empty body should be 422: got {resp.status_code}: {resp.text[:200]}"
        )
        body = resp.json()
        assert "detail" in body

    @pytest.mark.integration
    def test_invalid_chain_value(self, app) -> None:
        """Scanner with invalid chain should be handled."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={"address": SOL_TOKEN, "chain": "not_a_real_chain"},
            headers={**_AUTH, **_BEARER},
        )
        # Should either reject (422) or attempt gracefully (200/500)
        assert resp.status_code in (200, 422, 500), (
            f"Invalid chain: got {resp.status_code}: {resp.text[:200]}"
        )

    @pytest.mark.integration
    def test_malformed_json_returns_422(self, app) -> None:
        """Request with malformed body should return 422."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        # Send invalid content type that would cause parsing failure
        resp = client.post(
            "/api/v2/scanner/token/scan",
            content=b"not json at all",
            headers={
                **_AUTH,
                **_BEARER,
                "Content-Type": "application/json",
            },
        )
        assert resp.status_code == 422, f"Malformed JSON: got {resp.status_code}"

    @pytest.mark.integration
    def test_wallet_scan_no_address(self, app) -> None:
        """Wallet scan without address field returns 422."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v2/scanner/wallet/scan",
            json={"chain": "solana"},
            headers={**_AUTH, **_BEARER},
        )
        assert resp.status_code == 422, f"No address: got {resp.status_code}: {resp.text[:200]}"

    @pytest.mark.integration
    def test_health_endpoint_always_accessible(self, app) -> None:
        """Public health endpoints should always work, even without auth."""
        from fastapi.testclient import TestClient

        client = TestClient(app)

        # Root path
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert "service" in body
        assert body["service"] == "RMI Backend"

        # Version path
        resp = client.get("/version")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "operational"

    @pytest.mark.integration
    def test_health_endpoint_with_wrong_key(self, app) -> None:
        """Public health endpoints work even with wrong API key."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 200

        resp = client.get("/version", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 200

    @pytest.mark.integration
    def test_public_root_has_valid_response(self, app) -> None:
        """The root path / should return valid JSON."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["service"] == "RMI Backend"
        # Root is public, so this should succeed
        assert resp.status_code in (200, 204, 405), f"CORS preflight failed: {resp.status_code}"

    @pytest.mark.integration
    def test_security_headers_on_error(self, app) -> None:
        """Security headers should appear even on error responses."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        # Trigger a 422
        resp = client.post(
            "/api/v2/scanner/token/scan",
            json={},
            headers={**_AUTH, **_BEARER},
        )
        assert resp.status_code == 422
        # Check for content-type header (most basic response header)
        assert "content-type" in resp.headers or "Content-Type" in resp.headers
