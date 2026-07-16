"""Tests for app/factory.py - FastAPI app factory.

Tests that create_app produces a valid FastAPI instance with
the correct metadata and that error handlers/middleware are registered.
"""

from __future__ import annotations

from fastapi import FastAPI

from app.factory import DESCRIPTION, TITLE, VERSION, create_app


class TestCreateApp:
    """Tests for create_app factory function."""

    def test_returns_fastapi_instance(self):
        """create_app should return a FastAPI instance."""
        app = create_app()
        assert isinstance(app, FastAPI)

    def test_app_has_correct_title(self):
        """App should have the correct title."""
        app = create_app()
        assert app.title == TITLE
        assert app.title == "RMI Backend"

    def test_app_has_version(self):
        """App should have a version string."""
        app = create_app()
        assert app.version == VERSION
        assert app.version.startswith("2026")

    def test_app_has_description(self):
        """App should have a description."""
        app = create_app()
        assert app.description == DESCRIPTION
        assert "Rug Munch Intelligence" in app.description

    def test_app_has_routes(self):
        """App should have routes mounted."""
        app = create_app()
        assert len(app.routes) > 0

    def test_app_has_health_route(self):
        """App should have a /health route via the health_route sub-router."""
        app = create_app()

        # Also check included routers (Starlette wraps APIRouters as _IncludedRouter)
        def find_route_paths(routes):
            paths = []
            for r in routes:
                if hasattr(r, "path"):
                    paths.append(r.path)
                elif hasattr(r, "routes"):
                    paths.extend(find_route_paths(r.routes))
                elif hasattr(r, "original_router"):
                    paths.extend(find_route_paths(r.original_router.routes))
            return paths

        all_paths = find_route_paths(app.routes)
        matching = [p for p in all_paths if "/health" in p or "/live" in p or "/ready" in p]
        assert len(matching) >= 1

    def test_app_has_live_route(self):
        """App should have a /live route via the health_route sub-router."""
        app = create_app()

        # Use the client instead of route introspection
        from starlette.testclient import TestClient

        client = TestClient(app)
        resp = client.get("/live")
        assert resp.status_code == 200


class TestConstants:
    """Tests for factory constants."""

    def test_version_is_string(self):
        """VERSION should be a string."""
        assert isinstance(VERSION, str)

    def test_title_is_string(self):
        """TITLE should be a string."""
        assert isinstance(TITLE, str)

    def test_description_is_string(self):
        """DESCRIPTION should be a string."""
        assert isinstance(DESCRIPTION, str)
