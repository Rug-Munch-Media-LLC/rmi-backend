"""Tests for app/core/health.py"""

import asyncio

import pytest

from app.core.health import (
    DomainHealth,
    get_health_status,
    register_health_check,
    run_health_checks,
)


class TestDomainHealth:
    """Tests for DomainHealth dataclass."""

    def test_domain_health_creation(self):
        """Test creating a DomainHealth instance."""
        health = DomainHealth(name="test_domain", healthy=True, details={"key": "value"}, latency_ms=50)
        assert health.name == "test_domain"
        assert health.healthy is True
        assert health.details == {"key": "value"}
        assert health.latency_ms == 50
        assert health.error is None

    def test_domain_health_with_error(self):
        """Test DomainHealth with error message."""
        health = DomainHealth(name="test_domain", healthy=False, error="Connection failed")
        assert health.healthy is False
        assert health.error == "Connection failed"

    def test_domain_health_default_details(self):
        """Test DomainHealth with default empty details."""
        health = DomainHealth(name="test", healthy=True)
        assert health.details == {}


class TestHealthRegistry:
    """Tests for health check registry."""

    def test_register_health_check(self):
        """Test registering a health check function."""

        def mock_health_check():
            return DomainHealth(name="mock", healthy=True)

        register_health_check("mock", mock_health_check)
        assert "mock" in register_health_check.__globals__.get("_health_checks", {})

    def test_register_duplicate_health_check(self):
        """Test that duplicate registration is logged but allowed."""

        def mock_health_check1():
            return DomainHealth(name="dup", healthy=True)

        def mock_health_check2():
            return DomainHealth(name="dup", healthy=False)

        register_health_check("dup", mock_health_check1)
        register_health_check("dup", mock_health_check2)
        # Duplicate should be overwritten with the new one


class TestRunHealthChecks:
    """Tests for run_health_checks function."""

    @pytest.mark.asyncio
    async def test_run_health_checks_empty(self):
        """Test run_health_checks with no registered checks."""
        # Clear registry first
        from app.core.health import _health_checks

        _health_checks.clear()

        results = await run_health_checks()
        assert results == {}

    @pytest.mark.asyncio
    async def test_run_health_checks_sync(self):
        """Test run_health_checks with synchronous check."""
        from app.core.health import _health_checks

        _health_checks.clear()

        def sync_health_check():
            return DomainHealth(name="sync", healthy=True)

        register_health_check("sync", sync_health_check)
        results = await run_health_checks()

        assert "sync" in results
        assert results["sync"].healthy is True
        assert results["sync"].name == "sync"

    @pytest.mark.asyncio
    async def test_run_health_checks_async(self):
        """Test run_health_checks with async check."""
        from app.core.health import _health_checks

        _health_checks.clear()

        async def async_health_check():
            await asyncio.sleep(0)
            return DomainHealth(name="async", healthy=True, latency_ms=10)

        register_health_check("async", async_health_check)
        results = await run_health_checks()

        assert "async" in results
        assert results["async"].healthy is True
        assert results["async"].latency_ms is not None

    @pytest.mark.asyncio
    async def test_run_health_checks_failure(self):
        """Test run_health_checks handles exceptions."""
        from app.core.health import _health_checks

        _health_checks.clear()

        def failing_health_check():
            raise ValueError("Health check failed")

        register_health_check("failing", failing_health_check)
        results = await run_health_checks()

        assert "failing" in results
        assert results["failing"].healthy is False
        assert "fail" in results["failing"].error.lower()


class TestGetHealthStatus:
    """Tests for get_health_status function."""

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
