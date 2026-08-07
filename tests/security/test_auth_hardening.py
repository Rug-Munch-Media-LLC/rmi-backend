"""Auth hardening tests for the dual-token + consumer attribution model.

Covers the security(auth) changes from commit ab2695f5:
  - ``/api/v1/databus/fetch`` was removed from the public prefix whitelist —
    it now requires a Tier 1 shared secret (X-API-Key, X-RMI-Key, or Bearer).
  - ``/api/v1/news/_admin/*`` is a PROTECTED_SUBPATH under the public
    ``/api/v1/news/*`` prefix — it must 401 without a key.
  - Public product endpoints (market/overview, news/headlines) stay open.
  - ``X-Consumer-ID`` is echoed back on protected responses (consumer
    attribution).

These are integration-style tests against the real FastAPI app (see
``tests/security/conftest.py``). The databus ``fetch`` service is mocked
so the 200-with-key cases verify auth passes through to the handler.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from .conftest import TEST_INTERNAL_KEY, TEST_RMI_AUTH_TOKEN


@pytest.fixture
def databus_fetch_mock():
    """Stub the DataBus fetch service so authed requests return 200."""
    with patch(
        "app.databus.router.core.databus.fetch",
        new=AsyncMock(return_value={"ok": True, "data": "stub"}),
    ):
        yield


# ── databus/fetch is no longer public ──────────────────────────────────


class TestDatabusFetchRequiresAuth:
    """POST /api/v1/databus/fetch must require a Tier 1 shared secret."""

    def test_databus_fetch_returns_401_without_auth(self, client) -> None:
        resp = client.post("/api/v1/databus/fetch", json={"data_type": "token_price"})
        assert resp.status_code == 401, f"Expected 401 without auth, got {resp.status_code}: {resp.text[:200]}"

    def test_databus_fetch_returns_401_with_wrong_key(self, client) -> None:
        resp = client.post(
            "/api/v1/databus/fetch",
            json={"data_type": "token_price"},
            headers={"X-API-Key": "this-is-the-wrong-key"},
        )
        assert resp.status_code == 401, f"Expected 401 with wrong key, got {resp.status_code}"

    def test_databus_fetch_returns_200_with_x_api_key(self, client, databus_fetch_mock) -> None:
        resp = client.post(
            "/api/v1/databus/fetch",
            json={"data_type": "token_price"},
            headers={"X-API-Key": TEST_RMI_AUTH_TOKEN},
        )
        assert resp.status_code == 200, f"Expected 200 with X-API-Key, got {resp.status_code}: {resp.text[:200]}"
        assert resp.json()["ok"] is True

    def test_databus_fetch_returns_200_with_x_rmi_key(self, client, databus_fetch_mock) -> None:
        resp = client.post(
            "/api/v1/databus/fetch",
            json={"data_type": "token_price"},
            headers={"X-RMI-Key": TEST_INTERNAL_KEY},
        )
        assert resp.status_code == 200, f"Expected 200 with X-RMI-Key, got {resp.status_code}: {resp.text[:200]}"
        assert resp.json()["ok"] is True

    def test_databus_fetch_returns_200_with_bearer_shared_secret(self, client, databus_fetch_mock) -> None:
        resp = client.post(
            "/api/v1/databus/fetch",
            json={"data_type": "token_price"},
            headers={"Authorization": f"Bearer {TEST_RMI_AUTH_TOKEN}"},
        )
        assert resp.status_code == 200, f"Expected 200 with Bearer secret, got {resp.status_code}"


# ── news admin is a protected subpath ──────────────────────────────────


class TestNewsAdminProtectedSubpath:
    """/api/v1/news/_admin/* sits under the public /api/v1/news/* prefix but
    must still require auth (PROTECTED_SUBPATHS override)."""

    def test_news_admin_ingest_returns_401_without_auth(self, client) -> None:
        resp = client.post("/api/v1/news/_admin/ingest")
        assert resp.status_code == 401, f"Expected 401 without auth, got {resp.status_code}"

    def test_news_admin_ingest_returns_401_with_wrong_key(self, client) -> None:
        resp = client.post(
            "/api/v1/news/_admin/ingest",
            headers={"X-API-Key": "this-is-the-wrong-key"},
        )
        assert resp.status_code == 401, f"Expected 401 with wrong key, got {resp.status_code}"

    def test_news_admin_ingest_accepts_x_rmi_key(self, client) -> None:
        # ingest_all() hits real RSS feeds — we only assert the auth gate
        # passes (the request is authorized), not the ingest result.
        resp = client.post(
            "/api/v1/news/_admin/ingest",
            headers={"X-RMI-Key": TEST_INTERNAL_KEY},
        )
        assert resp.status_code != 401, "Authorized admin ingest must not be rejected by auth"


# ── public product endpoints stay open ────────────────────────────────


class TestPublicEndpointsStayOpen:
    """Auth hardening must not break the public product surface."""

    def test_market_overview_public(self, client) -> None:
        resp = client.get("/api/v1/market/overview")
        assert resp.status_code == 200, f"market/overview should be public, got {resp.status_code}"

    def test_news_headlines_public(self, client) -> None:
        resp = client.get("/api/v1/news/headlines")
        assert resp.status_code == 200, f"news/headlines should be public, got {resp.status_code}"

    def test_health_public(self, client) -> None:
        resp = client.get("/health")
        assert resp.status_code in (200, 404), f"/health should be public, got {resp.status_code}"


# ── consumer attribution ──────────────────────────────────────────────


class TestConsumerAttribution:
    """X-Consumer-ID is echoed back on protected responses."""

    def test_consumer_id_echoed_on_protected_endpoint(self, client, databus_fetch_mock) -> None:
        resp = client.post(
            "/api/v1/databus/fetch",
            json={"data_type": "token_price"},
            headers={
                "X-API-Key": TEST_RMI_AUTH_TOKEN,
                "X-Consumer-ID": "web-frontend",
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-Consumer-ID") == "web-frontend", (
            f"X-Consumer-ID should be echoed, got {resp.headers.get('X-Consumer-ID')}"
        )

    def test_no_consumer_header_no_echo(self, client, databus_fetch_mock) -> None:
        resp = client.post(
            "/api/v1/databus/fetch",
            json={"data_type": "token_price"},
            headers={"X-API-Key": TEST_RMI_AUTH_TOKEN},
        )
        assert resp.status_code == 200
        assert "X-Consumer-ID" not in resp.headers
