"""End-to-end tests for the OSINT aggregate API — HTTP → router → engine → report.

This suite exercises the *full* composition contract through the real
FastAPI app: the router (``app/routers/osint.py``) mounted at
``/api/osint``, the real orchestrator (``app/osint/engine.py``) with its
auto-detection, module applicability, ``ThreadPoolExecutor`` fan-out and
findings dedup, and the Pydantic response models. The only thing faked is
the *leaf* OSINT module lookups: ``app.osint.engine._load_lookup`` is
patched to return canned lookup functions, so no OSINT module, subprocess,
CLI or network call is ever made (offline + deterministic).

Covers:

* ``GET /api/osint/types`` — capability listing through the real engine;
* ``POST /api/osint/aggregate`` — domain (multi-module: domain +
  spiderfoot) and wallet (single-module) flows, asserting the combined
  ``results`` dict and the flat ``findings`` list are correctly shaped and
  deduped;
* ``GET /api/osint/{target_type}/{target}`` — the single-type path returns
  per-module findings (``run_osint`` omits the flat list, which the schema
  defaults to ``[]``; findings are surfaced inside ``results``).

Findings order from ``run_aggregate`` is nondeterministic (futures complete
out of order), so assertions match by value/set, never by list position.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

# Force env vars BEFORE any app imports (mirrors tests/test_routers.py).
os.environ["JWT_SECRET"] = "a" * 32
os.environ["ENVIRONMENT"] = "dev"
os.environ["RMI_AUTH_TOKEN"] = "test-auth-token-32-chars-long!!"
os.environ["DEV_BYPASS_AUTH"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.osint import OsintFinding, OsintReport, engine as osint_engine
from app.routers import osint as osint_router_mod

if TYPE_CHECKING:
    from collections.abc import Callable

# ── Canned report builders ───────────────────────────────────────────────────


def _report(
    target: str,
    target_type: str,
    *findings: dict[str, str],
    errors: list[str] | None = None,
) -> OsintReport:
    """Build an ``OsintReport`` from (source, category, summary, confidence) dicts."""
    report = OsintReport(target=target, target_type=target_type, errors=errors or [])
    for f in findings:
        report.findings.append(
            OsintFinding(
                source=f["source"],
                category=f["category"],
                summary=f["summary"],
                confidence=f.get("confidence", "medium"),
            )
        )
    return report


def _finding_dict(source: str, category: str, summary: str, confidence: str = "medium") -> dict[str, Any]:
    """The exact JSON shape the engine emits for a finding in a report dict."""
    return {"source": source, "category": category, "summary": summary, "confidence": confidence, "data": {}}


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

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(UnifiedRateLimiter, "acquire", _allow)
        yield


@pytest.fixture
def fake_lookup(monkeypatch: pytest.MonkeyPatch) -> dict[str, Callable[..., OsintReport]]:
    """Replace the engine's leaf-module loader with a keyed registry of canned lookups.

    Keys mirror ``_REGISTRY`` + ``spiderfoot``. This is the only mock: the
    router, engine composition, auto-detection and Pydantic validation all
    run for real.
    """
    registry: dict[str, Callable[..., OsintReport]] = {}

    def fake_load(module_path: str, func_name: str) -> Callable[..., OsintReport] | None:
        # Map (module_path, func_name) back to the registry key via _REGISTRY.
        for key, (path, fn) in osint_engine._REGISTRY.items():
            if path == module_path and fn == func_name:
                return registry.get(key)
        if module_path == osint_engine._SPIDERFOOT_MODULE[0] and func_name == osint_engine._SPIDERFOOT_MODULE[1]:
            return registry.get("spiderfoot")
        return None

    monkeypatch.setattr(osint_engine, "_load_lookup", fake_load)
    return registry


# ── GET /api/osint/types ─────────────────────────────────────────────────────


class TestGetTypesE2E:
    def test_types_lists_supported_targets_through_real_engine(self, client: TestClient) -> None:
        """GET /api/osint/types surfaces the real engine's TARGET_TYPES."""
        resp = client.get("/api/osint/types")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "target_types" in body
        types = body["target_types"]
        assert "domain" in types
        assert "wallet" in types
        assert len(types) == len(osint_engine.TARGET_TYPES)


# ── POST /api/osint/aggregate ────────────────────────────────────────────────


class TestAggregateDomainE2E:
    def test_domain_aggregate_combines_results_and_dedupes_findings(
        self, client: TestClient, fake_lookup: dict[str, Callable[..., OsintReport]], auth_headers: dict[str, str]
    ) -> None:
        """Domain aggregate: both modules' reports appear in ``results``; identical
        findings across modules collapse to one entry in the flat list."""
        fake_lookup["domain"] = lambda target, **_: _report(
            target,
            "domain",
            {"source": "whois", "category": "registrant", "summary": "registrar GoDaddy", "confidence": "high"},
            {"source": "dns", "category": "dns", "summary": "3 A record(s)", "confidence": "high"},
        )
        # spiderfoot returns one duplicate of the dns finding (must be deduped)
        # plus one unique finding of its own.
        fake_lookup["spiderfoot"] = lambda target, **_: _report(
            target,
            "domain",
            {"source": "dns", "category": "dns", "summary": "3 A record(s)", "confidence": "high"},
            {
                "source": "spiderfoot",
                "category": "ssl",
                "summary": "TLS cert for *.example.com",
                "confidence": "medium",
            },
        )

        resp = client.post(
            "/api/osint/aggregate",
            json={"target": "scam-example.com", "target_type": "domain"},
            headers=auth_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        # ── Top-level shape ──
        assert body["target"] == "scam-example.com"
        assert body["target_type"] == "domain"
        assert set(body["results"]) == {"domain", "spiderfoot"}
        assert body["errors"] == []

        # ── Per-module results are correctly shaped ──
        for module in ("domain", "spiderfoot"):
            module_report = body["results"][module]
            assert module_report["target"] == "scam-example.com"
            assert module_report["target_type"] == "domain"
            assert isinstance(module_report["findings"], list)
            assert module_report["findings"], f"{module} report should carry findings"
            assert module_report["errors"] == []

        # ── Flat findings: non-empty, deduped, correctly shaped ──
        flat = body["findings"]
        assert flat, "aggregate must return a non-empty flat findings list"
        for finding in flat:
            assert set(finding) == {"source", "category", "summary", "confidence", "data"}
        # The duplicate dns finding appears exactly once across the two modules.
        dns_findings = [f for f in flat if f["summary"] == "3 A record(s)"]
        assert dns_findings == [_finding_dict("dns", "dns", "3 A record(s)", "high")]
        summaries = {f["summary"] for f in flat}
        assert summaries == {"registrar GoDaddy", "3 A record(s)", "TLS cert for *.example.com"}


class TestAggregateWalletE2E:
    def test_wallet_aggregate_returns_single_module_report_with_findings(
        self, client: TestClient, fake_lookup: dict[str, Callable[..., OsintReport]], auth_headers: dict[str, str]
    ) -> None:
        """Wallet aggregate: only the wallet module applies (no spiderfoot fan-out)."""
        fake_lookup["wallet"] = lambda target, **_: _report(
            target,
            "wallet",
            {"source": "chainabuse", "category": "scam", "summary": "reported for phishing", "confidence": "high"},
        )

        resp = client.post(
            "/api/osint/aggregate",
            json={"target": "0xBE0eB53FC46b790099138e3d32C721856d41e865", "target_type": "wallet"},
            headers=auth_headers,
        )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["target"] == "0xBE0eB53FC46b790099138e3d32C721856d41e865"
        assert body["target_type"] == "wallet"
        assert set(body["results"]) == {"wallet"}
        assert body["findings"] == [_finding_dict("chainabuse", "scam", "reported for phishing", "high")]


# ── GET /api/osint/{target_type}/{target} ────────────────────────────────────


class TestGetSingleTypeE2E:
    def test_domain_get_returns_per_module_findings(
        self, client: TestClient, fake_lookup: dict[str, Callable[..., OsintReport]]
    ) -> None:
        """Single-type GET: run_osint surfaces findings inside ``results`` (the
        flat list is omitted by the engine and defaults to ``[]`` in the model)."""
        fake_lookup["domain"] = lambda target, **_: _report(
            target,
            "domain",
            {"source": "whois", "category": "registrant", "summary": "registrar GoDaddy", "confidence": "high"},
        )
        # spiderfoot unregistered -> engine records it as an error, not a crash.

        resp = client.get("/api/osint/domain/scam-example.com")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["target"] == "scam-example.com"
        assert body["target_type"] == "domain"
        assert set(body["results"]) == {"domain"}
        # Findings on the GET path live per-module inside `results`.
        domain_findings = body["results"]["domain"]["findings"]
        assert domain_findings, "GET path must return findings (per-module)"
        assert domain_findings == [_finding_dict("whois", "registrant", "registrar GoDaddy", "high")]
        assert any("spiderfoot" in e for e in body["errors"])
