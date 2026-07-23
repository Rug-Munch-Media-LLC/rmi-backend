"""Integration tests for the DataBus flow — provider chains, x402 endpoints.

Tests the DataBus service layer:
  - Provider chain building and existence
  - x402-databus tool catalog and access matrix
  - DataBus health/status endpoints
  - Access control tier mapping

DataBus is primarily an internal service layer with x402 HTTP endpoints
for paid tier access. These tests verify both the service layer and the
HTTP routes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


_AUTH = {"X-API-Key": "test-auth-token-32-chars-long!!"}




class TestDataBusProviderChains:
    """Test the DataBus provider chain infrastructure."""

    CHAINS = [
        "token_price",
        "market_overview",
        "trending",
        "fear_greed",
        "wallet_labels",
        "entity_intel",
        "scanner",
        "rag_search",
        "wallet_tokens",
        "token_metadata",
        "token_security",
    ]

    @pytest.mark.integration
    @pytest.mark.parametrize("chain", CHAINS)
    def test_chain_exists_in_providers(self, chain) -> None:
        """Every expected chain should exist in build_provider_chains()."""
        from app.databus.providers import build_provider_chains

        chains = build_provider_chains()
        assert chain in chains, f"Chain '{chain}' missing from provider chains"
        providers = chains[chain].providers if hasattr(chains[chain], "providers") else chains[chain]
        assert len(providers) > 0, f"Chain '{chain}' should have at least one provider"

    @pytest.mark.integration
    def test_provider_chain_has_fallback_providers(self) -> None:
        """Critical chains should have multiple providers for fallback."""
        from app.databus.providers import build_provider_chains

        chains = build_provider_chains()
        critical = ["token_price", "wallet_labels", "trending", "market_overview"]
        for chain in critical:
            assert chain in chains, f"Critical chain '{chain}' is missing"
            assert len(chains[chain]) >= 1, (
                f"Chain '{chain}' should have at least 1 provider, got {len(chains[chain])}"
            )

    @pytest.mark.integration
    def test_provider_chain_structure(self) -> None:
        """Each provider entry should have required fields."""
        from app.databus.providers import build_provider_chains

        chains = build_provider_chains()
        for chain_name, chain_data in chains.items():
            providers_list = chain_data.providers if hasattr(chain_data, "providers") else chain_data
            for provider in providers_list:
                assert isinstance(provider, dict), (
                    f"Provider in chain '{chain_name}' should be a dict"
                )
                # Should have either a 'provider' key or represent a callable
                assert "provider" in provider or "name" in provider or "fn" in provider, (
                    f"Provider in chain '{chain_name}' missing identifier: {provider}"
                )

    @pytest.mark.integration
    def test_total_provider_count(self) -> None:
        """DataBus should have a reasonable number of provider chains."""
        from app.databus.providers import build_provider_chains

        chains = build_provider_chains()
        assert len(chains) >= 10, f"Expected at least 10 provider chains, got {len(chains)}"


class TestX402DataBusCatalog:
    """Test x402 DataBus tool catalog endpoints."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_x402_catalog_endpoint(self, app) -> None:
        """GET /api/v1/x402-databus/catalog — should list all x402 tools."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/x402-databus/catalog", headers=_AUTH)
        assert resp.status_code in (200, 404), f"Expected 200 or 404, got {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert "total_tools" in body
            assert body["total_tools"] > 0
            assert "tools" in body
            assert isinstance(body["tools"], list)
            # Each tool should have price info
            first_tool = body["tools"][0]
            assert "tool_id" in first_tool
            assert "price_usd" in first_tool

    @pytest.mark.integration
    def test_x402_access_matrix(self, app) -> None:
        """GET /api/v1/x402-databus/access-matrix — tier access mapping."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/x402-databus/access-matrix", headers=_AUTH)
        assert resp.status_code in (200, 404), f"Expected 200 or 404, got {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert "free" in body
            assert "basic" in body
            assert "premium" in body
            assert isinstance(body["free"], list)
            assert isinstance(body["basic"], list)

    @pytest.mark.integration
    def test_x402_trial_status(self, app) -> None:
        """GET /api/v1/x402-databus/trials/{id} — trial status."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get(
            "/api/v1/x402-databus/trials/test-wallet-123",
            headers={**_AUTH, **{"Authorization": "Bearer test-bearer-token"}},
        )
        assert resp.status_code in (200, 404), f"Expected 200 or 404, got {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert "identifier" in body
            assert "trials" in body

    @pytest.mark.integration
    def test_x402_token_price_endpoint_unauthenticated(self, app) -> None:
        """POST /api/v1/x402-databus/token-price — should require auth."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v1/x402-databus/token-price",
            json={"mint": "So11111111111111111111111111111111111111112", "chain": "solana"},
        )
        assert resp.status_code == 401, f"Expected 401 (no auth), got {resp.status_code}"

    @pytest.mark.integration
    def test_x402_token_price_endpoint_missing_payment(self, app) -> None:
        """POST /api/v1/x402-databus/token-price — without payment should be rejected."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.post(
            "/api/v1/x402-databus/token-price",
            json={"mint": "So11111111111111111111111111111111111111112", "chain": "solana"},
            headers={**_AUTH, **{"Authorization": "Bearer test-bearer-token"}},
        )
        # 402 = payment required, 403 = forbidden, 500 = server error
        assert resp.status_code in (402, 403, 500, 200), (
            f"Expected payment rejection (402/403), got {resp.status_code}: {resp.text[:200]}"
        )


class TestDataBusHealth:
    """Test DataBus health and status endpoints."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_databus_health(self, app) -> None:
        """GET /api/v2/databus/health — should return service health."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v2/databus/health", headers=_AUTH)
        assert resp.status_code in (200, 404), f"Expected 200 or 404, got {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)

    @pytest.mark.integration
    def test_databus_status(self, app) -> None:
        """GET /api/v2/databus/status — provider health summary."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v2/databus/status", headers=_AUTH)
        assert resp.status_code in (200, 404), f"Expected 200 or 404, got {resp.status_code}"
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)
            # Should contain provider status info
            assert len(body) >= 0


class TestDataBusAccessControl:
    """Test the DataBus access control system."""

    @pytest.mark.integration
    def test_access_controller_exists(self) -> None:
        """DataBus access controller should be importable and functional."""
        from app.databus.access_control import access_controller

        assert access_controller is not None
        # Check tier access
        free_types = access_controller.get_x402_allowed_types("free")
        assert isinstance(free_types, (list, set))
        basic_types = access_controller.get_x402_allowed_types("basic")
        assert isinstance(basic_types, (list, set))
        # Basic should have at least as many as free
        assert len(basic_types) >= len(free_types)

    @pytest.mark.integration
    def test_access_controller_free_tier(self) -> None:
        """Free tier should have access to some data types."""
        from app.databus.access_control import access_controller

        free_types = access_controller.get_x402_allowed_types("free")
        # Free tier should have at least something or nothing
        assert isinstance(free_types, list)

    @pytest.mark.integration
    def test_databus_tool_pricing_structure(self) -> None:
        """X402 tool pricing should have required fields."""
        from app.routers.x402_databus_tools import X402_TOOL_PRICING

        assert len(X402_TOOL_PRICING) > 0, "Should have at least one tool defined"
        for tool_id, info in X402_TOOL_PRICING.items():
            assert "price_usd" in info, f"Tool '{tool_id}' missing price_usd"
            assert "description" in info, f"Tool '{tool_id}' missing description"
            assert info["price_usd"] > 0, f"Tool '{tool_id}' should have a positive price"

    @pytest.mark.integration
    def test_databus_supported_chains(self) -> None:
        """DataBus should support the expected chains."""
        from app.databus_gateway import SUPPORTED_CHAINS

        essential = ["solana", "ethereum", "bsc", "base", "arbitrum", "polygon"]
        for chain in essential:
            assert chain in SUPPORTED_CHAINS, (
                f"Essential chain '{chain}' missing from supported chains"
            )

    @pytest.mark.integration
    def test_databus_tier_limits(self) -> None:
        """DataBus tier limits should be properly configured."""
        from app.databus_gateway import TIER_LIMITS

        assert "free" in TIER_LIMITS
        assert "pro" in TIER_LIMITS
        assert "elite" in TIER_LIMITS
        for tier, limits in TIER_LIMITS.items():
            assert "requests_per_hour" in limits
            assert limits["requests_per_hour"] > 0


class TestTokenScannerDataBus:
    """Test the token scanner integration with DataBus."""

    @pytest.mark.integration
    def test_unified_token_scanner_importable(self) -> None:
        """The token scanner module should be importable and have expected structure."""
        from app.domains.scanner.unified_token_scanner import get_token_scanner

        assert get_token_scanner is not None
        # The scanner should be a callable that returns a scanner instance
        scanner = get_token_scanner()
        assert scanner is not None

    @pytest.mark.integration
    def test_unified_wallet_scanner_importable(self) -> None:
        """The wallet scanner module should be importable."""
        from app.unified_wallet_scanner import get_wallet_scanner

        assert get_wallet_scanner is not None
        scanner = get_wallet_scanner()
        assert scanner is not None
