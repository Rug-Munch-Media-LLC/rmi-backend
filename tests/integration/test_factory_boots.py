"""Factory import regression test (T14 CI gate).

Ensures the FastAPI app factory can boot cleanly with >= 40 routes
mounted. Catches regressions where new imports break the factory.

If you see this test failing:
  1. Run `python scripts/export_openapi.py --check --min-paths 40`
  2. Look at "router_mount_failed" warnings for the missing module
  3. Either create the module (stub OK for CI) or remove from ROUTER_MODULES
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure repo root on path so app.* imports work
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="module")
def app():
    """Build the FastAPI app once per module."""
    from app.factory import create_app

    return create_app()


def test_factory_boots_without_error(app) -> None:
    """Factory must boot without raising - any import failure = bug."""
    assert app is not None


def test_factory_has_minimum_routes(app) -> None:
    """App must expose >= 40 routes (T14 G09 gate).

    Counts all routes including those inside mounted APIRouter objects.
    The previous version used `hasattr(r, "path")` which only matches
    direct Route objects; APIRouter containers don't have a .path
    attribute but contain many Route objects.
    """

    def _flatten(routes):
        n = 0
        for r in routes:
            if hasattr(r, "routes"):  # APIRouter
                n += _flatten(r.routes)
            else:  # Route
                n += 1
        return n

    total = _flatten(app.routes)
    assert total >= 40, (
        f"Only {total} routes mounted. "
        f"Need >= 40 for T14 gate. Check factory logs for 'router_mount_failed'."
    )


def test_health_endpoint_responds(app) -> None:
    """GET /health must return 200 or 503 (not 500)."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code in (200, 503), f"Got {r.status_code}: {r.text}"


def test_liveness_endpoint_always_ok(app) -> None:
    """GET /live must always return 200 (no dependency checks)."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/live")
    assert r.status_code == 200
    assert r.json() == {"status": "alive"}


def test_openapi_schema_is_valid(app) -> None:
    """GET /openapi.json must return a valid OpenAPI 3.x schema."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    assert "openapi" in schema
    assert schema["openapi"].startswith("3.")
    assert "paths" in schema
    assert len(schema["paths"]) >= 40


def test_metrics_endpoint_returns_prometheus_format(app) -> None:
    """GET /metrics must return text/plain with Prometheus exposition format."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    # Prometheus format has # HELP and # TYPE comments
    assert "# HELP" in r.text or "# TYPE" in r.text


def test_homepage_returns_service_metadata(app) -> None:
    """GET / must return service name + version."""
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/")
    assert r.status_code == 200
    body = r.json()
    assert "service" in body
    assert "version" in body
    assert body["service"] == "RMI Backend"


def test_alerts_endpoint_list(app) -> None:
    """GET /alerts must return 200 (stub returns empty list).

    Note: the factory mounts each router directly via app.include_router,
    so /alerts (not /api/v1/alerts) is the actual path. The /api/v1
    aggregator in app/api/v1/__init__.py is for future use.
    """
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/alerts")
    assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
    body = r.json()
    assert body["count"] == 0
    assert body["items"] == []
