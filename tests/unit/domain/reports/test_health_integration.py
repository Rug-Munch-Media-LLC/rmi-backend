"""Tests for app/core/health.py - additional tests for complete coverage."""

import asyncio

import pytest

from app.core.health import (
    DomainHealth,
    get_health_status,
    register_health_check,
    run_health_checks,
)


class TestHealthIntegration:
    """Integration tests for health check system."""

    def test_get_health_status_healthy(self):
        """Test get_health_status when all checks pass."""
        from app.core.health import _health_checks
        _health_checks.clear()

        def healthy_check():
            return DomainHealth(name="test", healthy=True)

        register_health_check("test", healthy_check)
        status = get_health_status()

        assert status["status"] == "healthy"
        assert "test" in status["domains"]

    def test_get_health_status_degraded(self):
        """Test get_health_status when some checks fail."""
        from app.core.health import _health_checks
        _health_checks.clear()

        def healthy_check():
            return DomainHealth(name="ok", healthy=True)

        def failing_check():
            return DomainHealth(name="bad", healthy=False, error="Down")

        register_health_check("ok", healthy_check)
        register_health_check("bad", failing_check)
        status = get_health_status()

        assert status["status"] == "degraded"

    def test_get_health_status_unhealthy(self):
        """Test get_health_status when all checks fail."""
        from app.core.health import _health_checks
        _health_checks.clear()

        def failing_check1():
            return DomainHealth(name="bad1", healthy=False, error="Down 1")

        def failing_check2():
            return DomainHealth(name="bad2", healthy=False, error="Down 2")

        register_health_check("bad1", failing_check1)
        register_health_check("bad2", failing_check2)
        status = get_health_status()

        assert status["status"] == "unhealthy"
        assert len(status["domains"]) == 2


class TestRunHealthChecksAsync:
    """Async tests for run_health_checks."""

    @pytest.mark.asyncio
    async def test_run_health_checks_with_async_check(self):
        """Test run_health_checks with an async health check."""
        from app.core.health import _health_checks
        _health_checks.clear()

        async def async_health_check():
            await asyncio.sleep(0.001)  # Small delay
            return DomainHealth(name="async", healthy=True, latency_ms=5)

        register_health_check("async", async_health_check)
        results = await run_health_checks()

        assert "async" in results
        assert results["async"].healthy is True
        assert results["async"].latency_ms is not None

    @pytest.mark.asyncio
    async def test_run_health_checks_with_sync_check(self):
        """Test run_health_checks with a synchronous health check."""
        from app.core.health import _health_checks
        _health_checks.clear()

        def sync_health_check():
            return DomainHealth(name="sync", healthy=True)

        register_health_check("sync", sync_health_check)
        results = await run_health_checks()

        assert "sync" in results
        assert results["sync"].healthy is True

    @pytest.mark.asyncio
    async def test_run_health_checks_error_handling(self):
        """Test that run_health_checks handles exceptions gracefully."""
        from app.core.health import _health_checks
        _health_checks.clear()

        def failing_check():
            raise ValueError("Health check failed")

        register_health_check("failing", failing_check)
        results = await run_health_checks()

        assert "failing" in results
        assert results["failing"].healthy is False
        assert "failing" in results["failing"].error.lower() or "health check" in results["failing"].error.lower()


class TestDomainHealthEdgeCases:
    """Edge case tests for DomainHealth."""

    def test_domain_health_no_latency(self):
        """Test DomainHealth without latency."""
        health = DomainHealth(name="test", healthy=True)
        assert health.latency_ms is None

    def test_domain_health_empty_details(self):
        """Test DomainHealth with empty details."""
        health = DomainHealth(name="test", healthy=True, details={})
        assert health.details == {}

    def test_domain_health_large_details(self):
        """Test DomainHealth with large details dict."""
        health = DomainHealth(
            name="test",
            healthy=True,
            details={"key" + str(i): "value" + str(i) for i in range(100)}
        )
        assert len(health.details) == 100
