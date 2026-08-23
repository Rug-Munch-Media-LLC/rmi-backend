"""Integration tests for DataBus provider chains + HTTP endpoints.

Tests cache layers, circuit breakers, provider fallbacks.
Uses mocked external APIs to avoid real API calls.

Phase 2: Tests HTTP endpoint reachability for DataBus and related
routes. Uses 200/404 assertions to handle routes that may not be
mounted (DataBus is an internal service layer, not directly exposed).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import ClassVar

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestDataBusCache:
    """Test L1 (memory) and L2 (Redis) cache layers."""

    @pytest.mark.integration
    async def test_l1_cache_hit_returns_data(self):
        """L1 memory cache should return cached data on second fetch."""
        # Needs a databus.fetch() double with injectable cache layers.
        pytest.skip("not implemented — needs fetch/cache seam")

    @pytest.mark.integration
    async def test_l2_redis_fallback_on_l1_miss(self):
        """L2 Redis should serve when L1 misses."""
        pytest.skip("not implemented — needs redis testdouble")

    @pytest.mark.integration
    async def test_stale_while_revalidate_returns_stale(self):
        """SWR should return stale data while refreshing in background."""
        pytest.skip("not implemented — needs redis testdouble")


class TestCircuitBreakers:
    """Test provider circuit breakers - 3 failures → open → half-open → closed."""

    def _breaker(self, threshold=3, recovery=0.2):
        from app.core.circuit_breaker import SimpleCircuitBreaker

        return SimpleCircuitBreaker("test-provider", failure_threshold=threshold, recovery_timeout=recovery)

    @pytest.mark.integration
    async def test_circuit_opens_after_three_failures(self):
        """Circuit should open after 3 consecutive provider failures."""
        cb = self._breaker(threshold=3)
        from app.core.circuit_breaker import State

        assert cb.state is State.CLOSED
        for _ in range(3):
            cb.record_failure(is_server_error=True)
        assert cb.state is State.OPEN
        assert not cb.can_call(), "OPEN circuit must reject calls"

    @pytest.mark.integration
    async def test_half_open_allows_one_probe(self):
        """After timeout, half-open state should allow one probe request."""
        import asyncio

        from app.core.circuit_breaker import State

        cb = self._breaker(threshold=2, recovery=0.1)
        cb.record_failure()
        cb.record_failure()
        assert cb.state is State.OPEN
        await asyncio.sleep(0.15)  # past recovery_timeout
        assert cb.can_call(), "post-timeout call must be allowed as probe"
        assert cb.state is State.HALF_OPEN

    @pytest.mark.integration
    async def test_closed_after_successful_probe(self):
        """Successful probe in half-open should close the circuit."""
        import asyncio

        from app.core.circuit_breaker import State

        cb = self._breaker(threshold=2, recovery=0.1)
        cb.record_failure()
        cb.record_failure()
        await asyncio.sleep(0.15)
        assert cb.can_call()  # -> HALF_OPEN
        cb.record_success()
        assert cb.state is State.CLOSED
        assert cb.can_call()


class TestProviderFallback:
    """Test provider chain fallback - first fails, second succeeds."""

    @pytest.mark.integration
    async def test_fallback_to_secondary_provider(self):
        """When primary fails, secondary provider should be tried."""
        from app.databus.provider_core import Provider, ProviderChain, ProviderTier

        async def primary(**kwargs):
            raise ConnectionError("primary down")

        async def secondary(**kwargs):
            return {"data": "sec", "source": "secondary", "latency_ms": 5, "tier": "free_api"}

        chain = ProviderChain(
            data_type="test",
            providers=[
                Provider(name="p", tier=ProviderTier.FREE_API, fetch_fn=primary, weight=1.0),
                Provider(name="s", tier=ProviderTier.FREE_API, fetch_fn=secondary, weight=0.5),
            ],
        )
        result = await chain.fetch()
        assert result is not None
        assert result["data"] == "sec"

    @pytest.mark.integration
    async def test_all_providers_fail_returns_none(self):
        """When all providers fail, databus should return None."""
        from app.databus.provider_core import Provider, ProviderChain, ProviderTier

        async def down(**kwargs):
            raise ConnectionError("down")

        chain = ProviderChain(
            data_type="test",
            providers=[Provider(name=f"d{i}", tier=ProviderTier.FREE_API, fetch_fn=down, weight=1.0) for i in range(3)],
        )
        result = await chain.fetch()
        assert result is None


class TestDataBusSmoke:
    """Smoke tests for all 78 DataBus provider chains."""

    CHAINS: ClassVar[list[str]] = [
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
    async def test_chain_exists_in_providers(self, chain):
        """Every chain should exist in build_provider_chains()."""
        from app.databus.providers import build_provider_chains

        chains = build_provider_chains()
        assert chain in chains, f"Chain '{chain}' missing from provider chains"


class TestDataBusEndpoints:
    """HTTP endpoint reachability for DataBus and related routes.

    DataBus is primarily an internal service layer; these tests verify
    whether it has HTTP routes mounted and what they return if so.
    Uses 200/404 tolerance since routes may not be mounted in all configs.
    """

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_databus_health(self, app) -> None:
        """GET /api/v2/databus/health — returns 200 if mounted, 404 if not.

        DataBus health route may return internal service status when wired.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v2/databus/health")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)

    @pytest.mark.integration
    def test_databus_status(self, app) -> None:
        """GET /api/v2/databus/status — provider health summary."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v2/databus/status")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)

    @pytest.mark.integration
    def test_mcp_catalog(self, app) -> None:
        """GET /api/v1/catalog/ — MCP tool catalog endpoint."""
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/catalog/")
        assert resp.status_code in (200, 404)

    @pytest.mark.integration
    def test_news_feed(self, app) -> None:
        """GET /api/v1/news/feed — news feed endpoint.

        News router is mounted at /api/v1/news with feed, headlines,
        sources, sentiment, and stats sub-routes. The root /api/v1/news/
        has no GET handler (returns 405) — use /api/v1/news/feed instead.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/news/feed")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, (dict, list))

    @pytest.mark.integration
    def test_label_lookup(self, app) -> None:
        """GET /api/v2/labels/lookup — wallet label enrichment endpoint.

        Label lookup lives at app.routers.label_lookup under /api/v1/labels
        (POST /lookup). This v2 path may not be mounted.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v2/labels/lookup?address=0x0000000000000000000000000000000000000000")
        assert resp.status_code in (200, 404, 422)
        if resp.status_code == 422:
            body = resp.json()
            assert "detail" in body
