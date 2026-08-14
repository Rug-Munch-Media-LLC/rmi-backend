"""Integration tests for the OSINT REST API (``app/routers/osint.py``).

The router loads its engine (``app.osint.engine``) lazily into the module
slot ``_engine``; every test swaps that slot for a fake via monkeypatch, so
no OSINT module, subprocess, CLI or network call is ever made.

Covers:

* ``GET /api/osint/types`` — capability listing from the engine, plus the
  contract fallback when the engine is missing;
* ``GET /api/osint/{target_type}/{target}`` — success path (engine returns an
  ``OsintReport``), JSON-safe serialization of non-JSON values, and the error
  mapping table: 422 unknown type, 503 engine missing / function missing /
  ``OsintNotAvailable``, 500 ``OsintError`` / unexpected result, 422
  ``ValueError``, and the ``timeout`` signature-drift retry;
* ``POST /api/osint/aggregate`` — success path (with and without
  ``target_type``), the same 503/500/422 mapping, and Pydantic body
  validation (missing/invalid ``target`` -> 422);
* query validation: ``timeout`` outside the [5, 600] window -> 422.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

# Force env vars BEFORE any app imports (mirrors tests/test_routers.py).
os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["DEV_BYPASS_AUTH"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.osint import OsintError, OsintFinding, OsintNotAvailable, OsintReport
from app.routers import osint as osint_router_mod

# Target types the fake engine claims to support (matches the real engine).
FAKE_TARGET_TYPES: list[str] = ["username", "email", "domain", "wallet", "phone", "face"]


# ── Fakes ────────────────────────────────────────────────────────────────────


def _fake_engine(
    *,
    run_osint: Any = None,
    run_aggregate: Any = None,
    target_types: list[str] | None = None,
) -> SimpleNamespace:
    """Build a ``SimpleNamespace`` stand-in for ``app.osint.engine``."""
    return SimpleNamespace(
        TARGET_TYPES=target_types or FAKE_TARGET_TYPES,
        run_osint=run_osint,
        run_aggregate=run_aggregate,
    )


def _report(target: str, target_type: str, errors: list[str] | None = None) -> OsintReport:
    """Build a canned, empty ``OsintReport`` (findings are appended by callers)."""
    return OsintReport(target=target, target_type=target_type, errors=errors or [])


def _finding(source: str, category: str, summary: str, confidence: str = "medium", **data: Any) -> OsintFinding:
    """Build a canned ``OsintFinding``."""
    return OsintFinding(source=source, category=category, summary=summary, confidence=confidence, data=dict(data))


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def app() -> Any:
    """Fresh FastAPI app with the OSINT router mounted (prefix /api/osint)."""
    from app.factory import create_app

    app = create_app()
    app.include_router(osint_router_mod.osint_router, prefix="/api/osint", tags=["osint"])
    return app


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """API-key header that satisfies the CSRF middleware (service-to-service)."""
    return {"X-API-Key": "test-auth-token-32-chars-long!!"}


@pytest.fixture
def client(app: Any) -> Any:
    """Sync TestClient bound to the OSINT-enabled app."""
    client = TestClient(app)
    yield client
    client.close()


@pytest.fixture(autouse=True)
def _allow_rate_limits() -> Any:
    """Open the unified rate limiter so tests never hit 429."""
    from app.core.rate_limiter import UnifiedRateLimiter

    async def _allow(self: Any, key: str, rate: float = 1.0, burst: float = 5.0, tokens: float = 1.0) -> Any:
        return (True, {"X-RateLimit-Limit": int(burst), "X-RateLimit-Remaining": 1, "X-RateLimit-Reset": 0})

    with patch.object(UnifiedRateLimiter, "acquire", _allow):
        yield


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Install a fake engine module into the router's lazy-loaded slot."""
    engine = _fake_engine()
    monkeypatch.setattr(osint_router_mod, "_engine", engine)
    return engine


# ── GET /api/osint/types ─────────────────────────────────────────────────────


class TestGetTypes:
    def test_types_returns_engine_capability_listing(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """GET /api/osint/types mirrors the engine's TARGET_TYPES."""
        fake_engine.TARGET_TYPES = ["username", "domain"]
        resp = client.get("/api/osint/types")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"target_types": ["username", "domain"]}

    def test_types_uses_contract_fallback_when_engine_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Engine slot empty -> the router still lists the contract fallback."""
        monkeypatch.setattr(osint_router_mod, "_engine", None)
        resp = client.get("/api/osint/types")
        assert resp.status_code == 200, resp.text
        assert resp.json()["target_types"] == osint_router_mod.FALLBACK_TARGET_TYPES


# ── GET /api/osint/{target_type}/{target} ────────────────────────────────────


class TestGetReport:
    def test_get_report_returns_engine_report(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """Valid type + target -> 200 with the serialized OsintReport."""
        report = _report("victim@example.com", "email")
        report.findings.append(
            _finding(source="holehe", category="account", summary="account found on example.com", confidence="high")
        )
        fake_engine.run_osint = lambda target, target_type, **_: report

        resp = client.get("/api/osint/email/victim@example.com")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["target"] == "victim@example.com"
        assert body["target_type"] == "email"
        assert body["findings"] == [
            {
                "source": "holehe",
                "category": "account",
                "summary": "account found on example.com",
                "confidence": "high",
                "data": {},
            }
        ]

    def test_get_report_422_unknown_target_type(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """An unsupported target type is rejected with a helpful 422."""
        resp = client.get("/api/osint/bitcoin/abc")
        assert resp.status_code == 422, resp.text
        body = resp.json()
        assert "Unknown target type 'bitcoin'" in body["detail"]
        for valid in FAKE_TARGET_TYPES:
            assert valid in body["detail"]

    def test_get_report_503_engine_missing(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """Engine slot empty -> 503 with the availability detail."""
        monkeypatch.setattr(osint_router_mod, "_engine", None)
        resp = client.get("/api/osint/email/a@b.com")
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"] == "OSINT engine not available"

    def test_get_report_503_function_missing(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """Engine loaded but lacks run_osint -> 503 naming the missing function."""
        fake_engine.run_osint = None
        resp = client.get("/api/osint/email/a@b.com")
        assert resp.status_code == 503, resp.text
        assert "run_osint" in resp.json()["detail"]

    def test_get_report_maps_osint_not_available_to_503(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """Engine raising OsintNotAvailable (tool missing) -> 503."""
        fake_engine.run_osint = lambda *a, **k: (_ for _ in ()).throw(OsintNotAvailable("holehe not installed"))
        resp = client.get("/api/osint/email/a@b.com")
        assert resp.status_code == 503, resp.text
        assert "OSINT engine not available" in resp.json()["detail"]

    def test_get_report_maps_osint_error_to_500(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """Engine raising OsintError -> 500 with the message preserved."""
        fake_engine.run_osint = lambda *a, **k: (_ for _ in ()).throw(OsintError("upstream timeout"))
        resp = client.get("/api/osint/email/a@b.com")
        assert resp.status_code == 500, resp.text
        assert "upstream timeout" in resp.json()["detail"]

    def test_get_report_maps_value_error_to_422(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """Engine raising ValueError (garbage input) -> 422."""
        fake_engine.run_osint = lambda *a, **k: (_ for _ in ()).throw(ValueError("invalid domain name: 'x'"))
        resp = client.get("/api/osint/domain/x")
        assert resp.status_code == 422, resp.text
        assert "invalid domain name" in resp.json()["detail"]

    def test_get_report_500_on_unexpected_engine_result(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """Engine returning a non-report value -> 500, never a crash or silent body."""
        fake_engine.run_osint = lambda *a, **k: "not a report"
        resp = client.get("/api/osint/email/a@b.com")
        assert resp.status_code == 500, resp.text
        assert "unexpected result" in resp.json()["detail"]

    def test_get_report_serializes_non_json_values(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """Datetime values in finding data survive as strings (default=str)."""
        report = _report("example.com", "domain")
        report.findings.append(
            _finding(
                source="whois",
                category="registration",
                summary="registered",
                confidence="high",
                creation_date=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            )
        )
        fake_engine.run_osint = lambda target, target_type, **_: report

        resp = client.get("/api/osint/domain/example.com")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The router serializes with json.dumps(default=str) — str(datetime) uses a space separator.
        assert body["findings"][0]["data"]["creation_date"] == str(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))

    def test_get_report_retries_without_timeout_kwarg(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Engine accepting only positional args -> router retries without timeout."""
        calls: list[tuple[str, str]] = []

        def run_osint_positional(target: str, target_type: str) -> OsintReport:
            calls.append((target, target_type))
            return _report(target, target_type)

        monkeypatch.setattr(osint_router_mod, "_engine", _fake_engine(run_osint=run_osint_positional))
        resp = client.get("/api/osint/username/soxoj")
        assert resp.status_code == 200, resp.text
        assert calls == [("soxoj", "username")]

    def test_get_report_timeout_query_validation(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """timeout below the 5s floor is rejected by FastAPI query validation."""
        resp = client.get("/api/osint/email/a@b.com", params={"timeout": 2})
        assert resp.status_code == 422, resp.text

    def test_get_report_passes_timeout_to_engine(self, client: TestClient, fake_engine: SimpleNamespace) -> None:
        """The timeout query param reaches the engine call."""
        seen: dict[str, Any] = {}

        def run_osint(target: str, target_type: str, *, timeout: int) -> OsintReport:
            seen.update(target=target, target_type=target_type, timeout=timeout)
            return _report(target, target_type)

        fake_engine.run_osint = run_osint
        resp = client.get("/api/osint/username/soxoj", params={"timeout": 30})
        assert resp.status_code == 200, resp.text
        assert seen == {"target": "soxoj", "target_type": "username", "timeout": 30}


# ── POST /api/osint/aggregate ────────────────────────────────────────────────


class TestAggregate:
    def test_aggregate_returns_merged_report(
        self, client: TestClient, fake_engine: SimpleNamespace, auth_headers: dict[str, str]
    ) -> None:
        """POST with target + target_type -> 200 with the engine's aggregate shape."""
        report = _report("scam-example.com", "domain")
        report.findings.append(
            _finding(source="whois", category="registrant", summary="registrant information", confidence="medium")
        )
        fake_engine.run_aggregate = lambda target, *, timeout, target_type=None: report

        resp = client.post(
            "/api/osint/aggregate", json={"target": "scam-example.com", "target_type": "domain"}, headers=auth_headers
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["target"] == "scam-example.com"
        assert body["target_type"] == "domain"
        assert body["findings"][0]["source"] == "whois"

    def test_aggregate_omits_target_type_kwarg_when_absent(
        self, client: TestClient, fake_engine: SimpleNamespace, auth_headers: dict[str, str]
    ) -> None:
        """target_type None -> engine called without the kwarg (auto-detect)."""
        seen: dict[str, Any] = {}

        def run_aggregate(target: str, *, timeout: int) -> OsintReport:
            seen.update(target=target, timeout=timeout)
            return _report(target, "username")

        fake_engine.run_aggregate = run_aggregate
        resp = client.post("/api/osint/aggregate", json={"target": "soxoj"}, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert seen == {"target": "soxoj", "timeout": 60}
        assert "target_type" not in seen

    def test_aggregate_503_engine_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, auth_headers: dict[str, str]
    ) -> None:
        """Engine slot empty -> aggregate 503s like the single lookup."""
        monkeypatch.setattr(osint_router_mod, "_engine", None)
        resp = client.post("/api/osint/aggregate", json={"target": "soxoj"}, headers=auth_headers)
        assert resp.status_code == 503, resp.text

    def test_aggregate_maps_osint_not_available_to_503(
        self, client: TestClient, fake_engine: SimpleNamespace, auth_headers: dict[str, str]
    ) -> None:
        """Engine raising OsintNotAvailable -> 503 on aggregate too."""
        fake_engine.run_aggregate = lambda *a, **k: (_ for _ in ()).throw(OsintNotAvailable("spiderfoot offline"))
        resp = client.post(
            "/api/osint/aggregate", json={"target": "scam-example.com", "target_type": "domain"}, headers=auth_headers
        )
        assert resp.status_code == 503, resp.text
        assert "spiderfoot offline" in resp.json()["detail"]

    def test_aggregate_maps_osint_error_to_500(
        self, client: TestClient, fake_engine: SimpleNamespace, auth_headers: dict[str, str]
    ) -> None:
        """Engine raising OsintError -> 500 on aggregate."""
        fake_engine.run_aggregate = lambda *a, **k: (_ for _ in ()).throw(OsintError("aggregate failed"))
        resp = client.post("/api/osint/aggregate", json={"target": "soxoj"}, headers=auth_headers)
        assert resp.status_code == 500, resp.text
        assert "aggregate failed" in resp.json()["detail"]

    def test_aggregate_retries_without_timeout_kwarg(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, auth_headers: dict[str, str]
    ) -> None:
        """Aggregate engine accepting only positional args -> retry path works."""
        calls: list[tuple[str, str | None]] = []

        def run_aggregate_positional(target: str, target_type: str | None = None) -> OsintReport:
            calls.append((target, target_type))
            return _report(target, target_type or "username")

        monkeypatch.setattr(osint_router_mod, "_engine", _fake_engine(run_aggregate=run_aggregate_positional))
        resp = client.post(
            "/api/osint/aggregate", json={"target": "soxoj", "target_type": "email"}, headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        assert calls == [("soxoj", "email")]

    def test_aggregate_422_missing_target(
        self, client: TestClient, fake_engine: SimpleNamespace, auth_headers: dict[str, str]
    ) -> None:
        """Body without target -> Pydantic 422."""
        resp = client.post("/api/osint/aggregate", json={"target_type": "email"}, headers=auth_headers)
        assert resp.status_code == 422, resp.text

    def test_aggregate_422_invalid_body(
        self, client: TestClient, fake_engine: SimpleNamespace, auth_headers: dict[str, str]
    ) -> None:
        """Non-object body -> Pydantic 422."""
        resp = client.post("/api/osint/aggregate", json="nonsense", headers=auth_headers)
        assert resp.status_code == 422, resp.text
