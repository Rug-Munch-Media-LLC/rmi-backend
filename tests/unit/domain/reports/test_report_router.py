"""Tests for app/domain/reports/router.py - report router tests."""

from app.domains.reports.router import router


class TestReportRouter:
    """Tests for report router endpoints."""

    def test_router_has_generate_endpoint(self):
        """Test that the router has a POST /generate endpoint.

        The router is mounted at prefix /api/v1/reports, so the full
        path is /api/v1/reports/generate.
        """
        routes = [r.path for r in router.routes]
        assert "/api/v1/reports/generate" in routes

    def test_router_has_get_endpoint(self):
        """Test that the router has a GET /{report_id} endpoint.

        Full path: /api/v1/reports/{report_id}.
        """
        routes = [r.path for r in router.routes]
        assert "/api/v1/reports/{report_id}" in routes

    def test_router_tags(self):
        """Test that the router is tagged with 'reports'."""
        assert router.tags == ["reports"]
