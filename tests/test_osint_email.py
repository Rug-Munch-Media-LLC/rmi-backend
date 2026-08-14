"""Unit tests for ``app.osint.email`` — every HTTP source mocked.

``_get_json`` (the module's single HTTP entry point) is monkeypatched so no
network is touched. Covers HIBP breach/paste parsing, emailrep.io reputation
parsing, the local disposable-domain check, HTTP error mapping (404 silent,
429 rate-limited, 401/403 denied), network-error degradation, and the
input-validation path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    import pytest

from app.osint import OsintReport
from app.osint.email import (
    DISPOSABLE_DOMAINS,
    _add_breach_finding,
    _as_object_list,
    _emailrep_headers,
    _hibp_headers,
    _record_http_error,
    lookup_email,
)

# URL substrings used to route fake responses (the module formats its URLs
# with the quoted email, so template strings never appear in actual URLs).
_BREACH_URL = "breachedaccount"
_PASTE_URL = "pasteaccount"
_EREP_URL = "emailrep.io"


def _http_status_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://example.invalid")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"HTTP {status}", request=request, response=response)


def _script_get_json(
    monkeypatch: pytest.MonkeyPatch,
    payloads: dict[str, Any],
    errors: dict[str, Exception] | None = None,
) -> list[str]:
    """Patch ``_get_json`` to route by URL substring; returns the URLs hit."""
    errors = errors or {}
    hits: list[str] = []

    def fake_get_json(
        url: str, *, headers: dict[str, str], timeout: float, params: dict[str, str] | None = None
    ) -> Any:
        hits.append(url)
        for needle, err in errors.items():
            if needle in url:
                raise err
        for needle, payload in payloads.items():
            if needle in url:
                return payload
        raise AssertionError(f"no fake payload for {url}")

    monkeypatch.setattr("app.osint.email._get_json", fake_get_json)
    return hits


_BREACHES = [
    {
        "Name": "ExampleCorp",
        "Title": "ExampleCorp",
        "Domain": "example.com",
        "BreachDate": "2020-01-01",
        "PwnCount": 1234,
        "DataClasses": ["Email addresses", "Passwords"],
        "IsVerified": True,
        "IsFabricated": False,
        "IsSensitive": False,
        "IsSpamList": False,
    }
]

_PASTES = [{"Id": "abc123", "Source": "Pastebin", "Date": "2021-06-01", "Title": "leak", "EmailCount": 42}]

_EMAILREP = {
    "email": "victim@example.com",
    "reputation": "none",
    "suspicious": False,
    "references": 0,
    "details": {
        "blacklisted": False,
        "malicious_activity": False,
        "spam": False,
        "deliverable": True,
        "disposable": False,
        "free_provider": True,
        "valid_mx": True,
        "spoofable": True,
        "credentials_leaked": False,
        "data_breach": False,
    },
    "profiles": ["linkedin", "github"],
}


# ── Full lookup ─────────────────────────────────────────────────────────────


def test_lookup_email_full_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    hits = _script_get_json(
        monkeypatch,
        {_BREACH_URL: _BREACHES, _PASTE_URL: _PASTES, _EREP_URL: _EMAILREP},
    )

    report = lookup_email("victim@example.com", timeout=10)

    assert report.target == "victim@example.com"
    assert report.target_type == "email"
    assert report.errors == []
    assert len(hits) == 3

    breach = next(f for f in report.findings if f.category == "breach" and f.source == "hibp")
    assert "ExampleCorp" in breach.summary
    assert "1,234" in breach.summary  # PwnCount formatted with commas
    assert breach.data["data_classes"] == ["Email addresses", "Passwords"]

    paste = next(f for f in report.findings if f.category == "breach" and f.data.get("paste_id") == "abc123")
    assert paste.data["source_name"] == "Pastebin"

    reputation = next(f for f in report.findings if f.source == "emailrep" and f.category == "reputation")
    assert "none" in reputation.summary
    assert reputation.data["deliverable"] is True

    profiles = [f for f in report.findings if f.source == "emailrep" and "profile" in f.summary]
    assert len(profiles) == 1
    assert profiles[0].data["profiles"] == ["linkedin", "github"]


def test_lookup_email_suspicious_and_leaked(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        **_EMAILREP,
        "reputation": "poor",
        "suspicious": True,
        "details": {
            **_EMAILREP["details"],
            "credentials_leaked": True,
            "credentials_leaked_recent": True,
            "data_breach": True,
        },
    }
    _script_get_json(monkeypatch, {_BREACH_URL: [], _PASTE_URL: [], _EREP_URL: payload})
    report = lookup_email("victim@example.com", timeout=10)
    assert any("flagged suspicious" in f.summary for f in report.findings)
    leak = next(f for f in report.findings if f.source == "emailrep" and f.category == "breach")
    assert "recent" in leak.summary
    assert leak.data["credentials_leaked"] is True


def test_lookup_email_disposable_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_get_json(monkeypatch, {_BREACH_URL: [], _PASTE_URL: [], _EREP_URL: {}})
    report = lookup_email(f"victim@{next(iter(DISPOSABLE_DOMAINS))}", timeout=10)
    risk = next(f for f in report.findings if f.source == "disposable")
    assert risk.category == "risk"
    assert "disposable" in risk.summary


def test_lookup_email_invalid_address_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no network calls for invalid email")

    monkeypatch.setattr("app.osint.email._get_json", boom)
    report = lookup_email("not-an-email", timeout=10)
    assert report.errors == ["invalid email address: 'not-an-email'"]
    assert report.findings == []


# ── HTTP error mapping ──────────────────────────────────────────────────────


def test_hibp_404_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_get_json(
        monkeypatch,
        {_BREACH_URL: _BREACHES, _PASTE_URL: [], _EREP_URL: _EMAILREP},
        errors={_BREACH_URL: _http_status_error(404)},
    )
    report = lookup_email("victim@example.com", timeout=10)
    assert not any("hibp" in e for e in report.errors)
    assert any(f.source == "emailrep" for f in report.findings)


def test_hibp_429_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_get_json(
        monkeypatch,
        {_BREACH_URL: [], _PASTE_URL: [], _EREP_URL: {}},
        errors={_BREACH_URL: _http_status_error(429)},
    )
    report = lookup_email("victim@example.com", timeout=10)
    assert any("rate limited (429)" in e for e in report.errors)


def test_hibp_401_access_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_get_json(
        monkeypatch,
        {_BREACH_URL: [], _PASTE_URL: [], _EREP_URL: {}},
        errors={_BREACH_URL: _http_status_error(401)},
    )
    report = lookup_email("victim@example.com", timeout=10)
    assert any("access denied (401)" in e for e in report.errors)


def test_hibp_request_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_get_json(
        monkeypatch,
        {_BREACH_URL: [], _PASTE_URL: [], _EREP_URL: {}},
        errors={_BREACH_URL: httpx.ConnectError("connection refused")},
    )
    report = lookup_email("victim@example.com", timeout=10)
    assert any("hibp: request failed" in e for e in report.errors)


def test_record_http_error_mapping() -> None:
    report = OsintReport(target="x", target_type="email")
    _record_http_error(report, "hibp", _http_status_error(404))
    assert report.errors == []  # 404 = no records, silent
    _record_http_error(report, "hibp", _http_status_error(500))
    assert any("HTTP 500" in e for e in report.errors)


# ── Parsing helpers ─────────────────────────────────────────────────────────


def test_emailrep_unexpected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    _script_get_json(monkeypatch, {_BREACH_URL: [], _PASTE_URL: [], _EREP_URL: ["not-a-dict"]})
    report = lookup_email("victim@example.com", timeout=10)
    assert any("emailrep: unexpected response shape" in e for e in report.errors)


def test_as_object_list() -> None:
    assert _as_object_list([{"a": 1}, "junk", 42]) == [{"a": 1}]
    assert _as_object_list("not-a-list") == []


def test_add_breach_finding_unknown_pwn_count() -> None:
    report = OsintReport(target="x", target_type="email")
    _add_breach_finding(report, {"Name": "X", "Domain": "x.com", "BreachDate": "2020"})
    assert "unknown accounts" in report.findings[0].summary


def test_headers_include_api_keys_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIBP_API_KEY", "hibp-key")
    monkeypatch.setenv("EMAILREP_API_KEY", "erep-key")
    assert _hibp_headers()["hibp-api-key"] == "hibp-key"
    assert _emailrep_headers()["X-API-Key"] == "erep-key"
    assert "User-Agent" in _hibp_headers()


def test_headers_omit_api_keys_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HIBP_API_KEY", raising=False)
    monkeypatch.delenv("EMAILREP_API_KEY", raising=False)
    assert "hibp-api-key" not in _hibp_headers()
    assert "X-API-Key" not in _emailrep_headers()
