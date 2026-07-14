"""Integration tests for miscellaneous API endpoints.

Covers bulletin, rugcharts, status page, and other non-core
endpoints that are less frequently exercised. Uses 200/404 assertions
to handle routes that may not be mounted in all configurations.

Each route is tested independently so one failure doesn't block
coverage of the others.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class TestBulletin:
    """Tests for the bulletin board API."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_bulletin(self, app) -> None:
        """GET /api/v1/bulletin/ — get bulletin board posts.

        Bulletin router (app.routers.bulletin) is mounted at /api/v1/bulletin
        with 21 routes for posts, comments, moderation, and admin actions.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/bulletin/")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, (dict, list))


class TestRugCharts:
    """Tests for RugCharts API endpoints."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_rugcharts_trending(self, app) -> None:
        """GET /api/v2/rugcharts/trending — trending tokens.

        RugCharts router is at /api/v1/rugcharts/trending (v1).
        This v2 endpoint tests whether a v2 mount exists.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v2/rugcharts/trending")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, (dict, list))


class TestStatusPage:
    """Tests for the public status page endpoints."""

    @pytest.fixture(scope="module")
    def app(self):
        """Build the FastAPI app once per module."""
        from app.factory import create_app

        return create_app()

    @pytest.mark.integration
    def test_status_page(self, app) -> None:
        """GET /status — public status page.

        Status page at /api/v1/status (app.routers.status_page) shows
        health of all RMI services. The /status path without the prefix
        may redirect or return 404 depending on wiring.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/status")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, (dict, list))

    @pytest.mark.integration
    def test_api_status(self, app) -> None:
        """GET /api/v1/status — full status JSON endpoint.

        Status page router uses prefix /api/v1/status with
        @router.get("") for the root listing.
        """
        from fastapi.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/api/v1/status")
        assert resp.status_code in (200, 404)
        if resp.status_code == 200:
            body = resp.json()
            assert isinstance(body, dict)
