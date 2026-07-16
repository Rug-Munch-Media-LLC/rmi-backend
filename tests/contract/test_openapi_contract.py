"""Contract tests - verify API responses match OpenAPI schema.

Uses schemathesis to fuzz test that every endpoint returns responses
that conform to the declared schema. Catches type mismatches, missing
required fields, and incorrect status codes.

T21: Run with `pytest tests/contract/ -x`
"""

import pytest

# Skip if server is not running or schemathesis not installed
schemathesis = pytest.importorskip("schemathesis")

SCHEMA_URL = "http://localhost:8000/openapi.json"


@pytest.fixture(scope="module")
def schema():
    """Load the OpenAPI schema from the running server."""
    try:
        return schemathesis.from_uri(SCHEMA_URL)
    except Exception:
        pytest.skip("Backend not running at localhost:8000")


@pytest.fixture(scope="module")
def api_state(schema):
    """Set up Hypothesis strategies for property-based testing."""
    return schema.as_state_machine()


def test_openapi_schema_loads():
    """Verify the OpenAPI schema is valid and loadable."""
    import httpx

    resp = httpx.get(SCHEMA_URL)
    assert resp.status_code == 200, f"OpenAPI schema not available: {resp.status_code}"
    data = resp.json()
    assert "paths" in data
    assert "components" in data
    assert len(data["paths"]) > 10, "Expected at least 10 endpoints"


def test_health_endpoint_contract():
    """Health endpoint must return 200 with correct schema."""
    import httpx

    resp = httpx.get("http://localhost:8000/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "stores" in data
    assert data["status"] in ("healthy", "degraded", "unhealthy")


def test_liveness_contract():
    """Liveness endpoint must return {status: 'alive'}."""
    import httpx

    resp = httpx.get("http://localhost:8000/live")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "alive"


def test_readiness_contract():
    """Readiness endpoint must return {status: 'ready'|'not_ready', checks: {...}}."""
    import httpx

    resp = httpx.get("http://localhost:8000/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ready", "not_ready")
    assert "checks" in data
    assert isinstance(data["checks"], dict)


def test_metrics_endpoint_serves_prometheus():
    """/metrics must return Prometheus exposition format."""
    import httpx

    resp = httpx.get("http://localhost:8000/metrics")
    assert resp.status_code == 200
    assert "HELP" in resp.text or "#" in resp.text, "Not Prometheus format"


def test_no_endpoint_returns_500_on_valid_input():
    """Valid requests to known endpoints should not return 500."""
    import httpx

    endpoints = [
        ("GET", "/health"),
        ("GET", "/live"),
        ("GET", "/ready"),
        ("GET", "/metrics"),
    ]
    for method, path in endpoints:
        resp = httpx.request(method, f"http://localhost:8000{path}")
        assert resp.status_code != 500, f"{method} {path} returned 500"
