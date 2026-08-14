"""Unit tests for ``app.osint.engine`` — the OSINT orchestrator.

``_load_lookup`` (the module loader) is monkeypatched to return canned lookup
functions, so no OSINT module, subprocess, CLI or network call is made. The
only real code exercised is the engine's own auto-detection, dispatch,
concurrency and merge/dedup logic.

Covers:

* ``detect_target_type`` — auto-detection for email / phone / wallet (0x and
  base58) / domain / username, plus explicit type passthrough;
* ``run_osint`` — single-type dispatch (email/phone/wallet/face run exactly
  their module; domain/username additionally run SpiderFoot), error
  collection for unavailable/failing/wrong-typed modules, and the unknown
  target_type path (never raises);
* ``run_aggregate`` — parallel execution, findings merged across modules,
  dedup by (source, category, summary, confidence), module errors merged and
  deduped, and the unknown target_type path.

NOTE: ``run_aggregate`` completes module futures in nondeterministic order
(``ThreadPoolExecutor``), so assertions never depend on ``results`` key order
or ``findings`` ordering — they match by value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.osint import OsintFinding, OsintReport, engine as osint_engine
from app.osint.engine import detect_target_type, run_aggregate, run_osint

if TYPE_CHECKING:
    from collections.abc import Callable

# ── Canned reports ───────────────────────────────────────────────────────────


def _report(target: str, target_type: str, *findings: dict[str, str], errors: list[str] | None = None) -> OsintReport:
    """Build an ``OsintReport`` from (source, category, summary, confidence) dicts.

    ``OsintFinding`` is constructed directly (not via ``OsintReport.add``)
    because ``add`` does not accept a ``confidence`` — it always defaults to
    ``medium``.
    """
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


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_lookup(monkeypatch: pytest.MonkeyPatch) -> dict[str, Callable[..., OsintReport]]:
    """Replace ``_load_lookup`` with a keyed registry of canned lookups.

    Keys mirror ``_REGISTRY`` + ``spiderfoot``. Lookups accept the same
    (target, *, timeout=...) / (target, *, target_type=..., timeout=...)
    call shapes the engine uses.
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


# ── detect_target_type ───────────────────────────────────────────────────────


class TestDetectTargetType:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("victim@example.com", "email"),
            ("VICTIM@Example.COM", "email"),
            ("+15551234567", "phone"),
            ("555-123-4567", "phone"),
            ("(555) 123 4567", "phone"),
            ("1234567", "phone"),
            ("0xAbC1234567890AbC1234567890AbC1234567890", "wallet"),
            ("So11111111111111111111111111111111111111112", "wallet"),
            ("example.com", "domain"),
            ("sub.example.co.uk", "domain"),
            ("Scam-Example.COM", "domain"),
            ("elonmusk", "username"),
            ("some handle", "username"),
            ("123", "username"),  # too short for a phone number
        ],
    )
    def test_auto_detection(self, value: str, expected: str) -> None:
        assert detect_target_type(value) == expected

    def test_explicit_target_type_passthrough(self) -> None:
        """An explicit target_type is returned unchanged (caller validates)."""
        assert detect_target_type("not-a-real-type", "email") == "email"
        assert detect_target_type("example.com", "username") == "username"

    def test_email_wins_over_phone_and_domain(self) -> None:
        """Order matters: '@' beats digit runs and dots."""
        assert detect_target_type("user+555@example.com") == "email"


# ── run_osint ────────────────────────────────────────────────────────────────


class TestRunOsint:
    def test_email_runs_only_email_module(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        fake_lookup["email"] = lambda target, **_: _report(
            target, "email", {"source": "holehe", "category": "account", "summary": "found", "confidence": "high"}
        )
        result = run_osint("victim@example.com")
        assert result["target_type"] == "email"
        assert list(result["results"]) == ["email"]
        assert result["results"]["email"]["findings"][0]["source"] == "holehe"
        assert result["errors"] == []

    def test_domain_runs_domain_plus_spiderfoot(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        fake_lookup["domain"] = lambda target, **_: _report(
            target, "domain", {"source": "whois", "category": "registrant", "summary": "registrant info"}
        )
        fake_lookup["spiderfoot"] = lambda target, **_: _report(
            target, "domain", {"source": "spiderfoot", "category": "dns", "summary": "A records"}
        )
        result = run_osint("example.com")
        assert result["target_type"] == "domain"
        assert set(result["results"]) == {"domain", "spiderfoot"}
        assert result["errors"] == []

    def test_username_runs_username_plus_spiderfoot(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        fake_lookup["username"] = lambda target, **_: _report(
            target, "username", {"source": "maigret", "category": "profile", "summary": "found on twitter"}
        )
        fake_lookup["spiderfoot"] = lambda target, **_: _report(
            target, "username", {"source": "spiderfoot", "category": "profile", "summary": "found on github"}
        )
        result = run_osint("soxoj")
        assert result["target_type"] == "username"
        assert set(result["results"]) == {"username", "spiderfoot"}

    def test_wallet_runs_only_wallet_module(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        fake_lookup["wallet"] = lambda target, **_: _report(
            target, "wallet", {"source": "chainabuse", "category": "scam", "summary": "reported scam"}
        )
        result = run_osint("So11111111111111111111111111111111111111112")
        assert result["target_type"] == "wallet"
        assert list(result["results"]) == ["wallet"]

    def test_face_runs_only_face_module(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        fake_lookup["face"] = lambda target, **_: _report(
            target, "face", {"source": "face", "category": "reverse_search", "summary": "matches"}
        )
        result = run_osint("https://img.example.com/face.jpg", "face")
        assert result["target_type"] == "face"
        assert list(result["results"]) == ["face"]

    def test_explicit_target_type_is_used(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        fake_lookup["domain"] = lambda target, **_: _report(target, "domain")
        result = run_osint("https://example.com/order?id=1", "domain")
        assert result["target_type"] == "domain"
        assert result["results"]["domain"]["target"] == "https://example.com/order?id=1"

    def test_unavailable_module_recorded_in_errors(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        """Missing module -> error entry; the engine still returns a report."""
        result = run_osint("victim@example.com")
        assert result["results"] == {}
        assert len(result["errors"]) == 1
        assert "module not available: email" in result["errors"][0]

    def test_failing_module_recorded_in_errors(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        """A raising module never crashes the engine — it becomes an error."""

        def boom(target: str, **_: Any) -> OsintReport:
            raise RuntimeError("network error")

        fake_lookup["email"] = boom
        result = run_osint("victim@example.com")
        assert result["results"] == {}
        assert any("email: RuntimeError: network error" in e for e in result["errors"])

    def test_wrong_return_type_recorded_in_errors(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        """A lookup returning a non-OsintReport is flagged, not trusted."""

        fake_lookup["email"] = lambda target, **_: {"findings": []}
        result = run_osint("victim@example.com")
        assert result["results"] == {}
        assert any("expected OsintReport" in e for e in result["errors"])

    def test_domain_spiderfoot_failure_keeps_domain_results(
        self, fake_lookup: dict[str, Callable[..., OsintReport]]
    ) -> None:
        """One module failing (spiderfoot) does not drop the other's results."""
        fake_lookup["domain"] = lambda target, **_: _report(
            target, "domain", {"source": "whois", "category": "registrant", "summary": "registrar X"}
        )

        def boom(target: str, **_: Any) -> OsintReport:
            raise TimeoutError("scan timed out")

        fake_lookup["spiderfoot"] = boom
        result = run_osint("example.com")
        assert set(result["results"]) == {"domain"}
        assert any("spiderfoot: TimeoutError" in e for e in result["errors"])

    def test_unknown_target_type_returns_error(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        """An unresolvable target type -> error entry, never a raise."""
        result = run_osint("x", "bitcoin")
        assert result["target_type"] == "bitcoin"
        assert result["results"] == {}
        assert "unknown target_type: 'bitcoin'" in result["errors"]


# ── run_aggregate ────────────────────────────────────────────────────────────


class TestRunAggregate:
    def test_merges_findings_from_all_modules(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        fake_lookup["domain"] = lambda target, **_: _report(
            target,
            "domain",
            {"source": "whois", "category": "registrant", "summary": "registrar GoDaddy", "confidence": "medium"},
            {"source": "dns", "category": "dns", "summary": "3 A record(s)", "confidence": "high"},
        )
        fake_lookup["spiderfoot"] = lambda target, **_: _report(
            target,
            "domain",
            {"source": "spiderfoot", "category": "dns", "summary": "5 A record(s)", "confidence": "medium"},
        )
        result = run_aggregate("example.com")
        assert result["target_type"] == "domain"
        assert set(result["results"]) == {"domain", "spiderfoot"}
        summaries = {f["summary"] for f in result["findings"]}
        assert summaries == {"registrar GoDaddy", "3 A record(s)", "5 A record(s)"}

    def test_deduplicates_identical_findings_across_modules(
        self, fake_lookup: dict[str, Callable[..., OsintReport]]
    ) -> None:
        """Same (source, category, summary, confidence) appears once."""
        finding = {"source": "whois", "category": "registrant", "summary": "registrar GoDaddy", "confidence": "high"}
        fake_lookup["domain"] = lambda target, **_: _report(target, "domain", finding)
        fake_lookup["spiderfoot"] = lambda target, **_: _report(target, "domain", finding)
        result = run_aggregate("example.com")
        assert [f for f in result["findings"] if f["summary"] == "registrar GoDaddy"] == [
            {
                "source": "whois",
                "category": "registrant",
                "summary": "registrar GoDaddy",
                "confidence": "high",
                "data": {},
            }
        ]

    def test_keeps_findings_that_differ_only_by_confidence(
        self, fake_lookup: dict[str, Callable[..., OsintReport]]
    ) -> None:
        """Confidence is part of the dedup key — different ratings are kept."""
        fake_lookup["domain"] = lambda target, **_: _report(
            target, "domain", {"source": "dns", "category": "dns", "summary": "3 A record(s)", "confidence": "high"}
        )
        fake_lookup["spiderfoot"] = lambda target, **_: _report(
            target, "domain", {"source": "dns", "category": "dns", "summary": "3 A record(s)", "confidence": "medium"}
        )
        result = run_aggregate("example.com")
        assert len([f for f in result["findings"] if f["summary"] == "3 A record(s)"]) == 2

    def test_merges_module_errors_and_dedupes(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        fake_lookup["domain"] = lambda target, **_: _report(target, "domain", errors=["dns: timeout"])

        def boom(target: str, **_: Any) -> OsintReport:
            raise RuntimeError("spiderfoot offline")

        fake_lookup["spiderfoot"] = boom
        result = run_aggregate("example.com")
        assert any("domain: dns: timeout" in e for e in result["errors"])
        assert any("spiderfoot: RuntimeError: spiderfoot offline" in e for e in result["errors"])
        # Module-level error + engine-level note for the same key collapse to one entry.
        assert result["errors"] == list(dict.fromkeys(result["errors"]))

    def test_unavailable_modules_do_not_break_others(self, fake_lookup: dict[str, Callable[..., OsintReport]]) -> None:
        fake_lookup["domain"] = lambda target, **_: _report(
            target, "domain", {"source": "whois", "category": "registrant", "summary": "registrar X"}
        )
        result = run_aggregate("example.com")
        assert set(result["results"]) == {"domain"}
        assert any("module not available: spiderfoot" in e for e in result["errors"])

    def test_unknown_target_type_returns_empty_aggregate(
        self, fake_lookup: dict[str, Callable[..., OsintReport]]
    ) -> None:
        result = run_aggregate("x", "bitcoin")
        assert result["target_type"] == "bitcoin"
        assert result["results"] == {}
        assert result["findings"] == []
        assert "unknown target_type: 'bitcoin'" in result["errors"]


# ── Registry sanity (guards the mocked fixture's key mapping) ────────────────


class TestRegistryShape:
    def test_registry_covers_all_target_types(self) -> None:
        """Every declared TARGET_TYPES entry has a registry (module, func) pair."""
        assert set(osint_engine._REGISTRY) == set(osint_engine.TARGET_TYPES)

    def test_spiderfoot_dispatched_only_for_domain_and_username(self) -> None:
        """The general engine is only fan-out applicable for domain/username."""
        assert frozenset({"domain", "username"}) == osint_engine._SPIDERFOOT_TYPES
        for t in osint_engine.TARGET_TYPES:
            if t in osint_engine._SPIDERFOOT_TYPES:
                continue
            assert t in osint_engine._REGISTRY  # every non-spiderfoot type has its own module
