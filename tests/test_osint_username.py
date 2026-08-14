"""Unit tests for ``app.osint.username`` — sherlock/maigret subprocess mocked.

Neither CLI is spawned: ``_require_binary`` and ``_run`` are monkeypatched so
``_sherlock_rows`` reads a canned CSV written into its temp cwd, and the
maigret fallback (``app.osint_tools.lookup_username``) is replaced with a
canned :class:`OsintResult`. Covers:

* input validation (empty, email-like, whitespace, bad ``top_sites``/timeout);
* sherlock primary path — CSV parsing into findings, ``--site`` flags, and the
  claimed/not-found row filter;
* fallback to maigret when sherlock is missing or errors;
* ``OsintNotAvailable`` when neither engine exists;
* maigret failure recorded as an error without crashing.
"""

from __future__ import annotations

import csv
import os
import subprocess

import pytest

from app.osint import OsintError as AppOsintError, OsintNotAvailable as AppOsintNotAvailable, OsintReport
from app.osint.username import (
    DEFAULT_SITES,
    _add_maigret_findings,
    _add_sherlock_findings,
    _sherlock_rows,
    lookup_username,
)
from app.osint_tools import OsintError as MaigretError, OsintNotAvailable as MaigretNotAvailable, OsintResult, OsintSite

# ── Input validation ────────────────────────────────────────────────────────


def test_lookup_username_rejects_empty() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        lookup_username("   ")


def test_lookup_username_rejects_email() -> None:
    with pytest.raises(ValueError, match="email"):
        lookup_username("victim@example.com")


def test_lookup_username_rejects_whitespace_and_separators() -> None:
    for bad in ("has space", "has/slash", "has\\slash"):
        with pytest.raises(ValueError, match="invalid username"):
            lookup_username(bad)


def test_lookup_username_rejects_bad_options() -> None:
    with pytest.raises(ValueError, match="top_sites"):
        lookup_username("okuser", top_sites=0)
    with pytest.raises(ValueError, match="timeout"):
        lookup_username("okuser", timeout=0)


# ── Sherlock helpers ────────────────────────────────────────────────────────


def _install_sherlock_cli(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]]) -> list[list[str]]:
    """Fake ``_require_binary`` + ``_run`` (writes CSV into the temp cwd)."""
    argv_calls: list[list[str]] = []
    monkeypatch.setattr("app.osint.username._require_binary", lambda name, hint: f"/fake/bin/{name}")

    def fake_run(argv: list[str], *, cwd: str, timeout: int) -> subprocess.CompletedProcess[str]:
        argv_calls.append(argv)
        path = os.path.join(cwd, "report.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=["username", "name", "url_main", "url_user", "exists", "http_status", "response_time_s"]
            )
            writer.writeheader()
            writer.writerows(rows)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("app.osint.username._run", fake_run)
    return argv_calls


def _install_maigret(
    monkeypatch: pytest.MonkeyPatch, result: OsintResult | None = None, exc: Exception | None = None
) -> None:
    def fake_maigret(username: str, *, timeout: int, top_sites: int) -> OsintResult:
        if exc is not None:
            raise exc
        assert result is not None
        return result

    monkeypatch.setattr("app.osint.username.maigret_lookup", fake_maigret)


def test_sherlock_rows_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_sherlock_cli(
        monkeypatch,
        [
            {
                "username": "soxoj",
                "name": "GitHub",
                "url_main": "https://github.com",
                "url_user": "https://github.com/soxoj",
                "exists": "claimed",
                "http_status": "200",
                "response_time_s": "1.5",
            },
            {
                "username": "soxoj",
                "name": "Reddit",
                "url_main": "https://reddit.com",
                "url_user": "https://reddit.com/u/soxoj",
                "exists": "not found",
                "http_status": "?",
                "response_time_s": "?",
            },
        ],
    )
    rows = _sherlock_rows("soxoj", sites=["GitHub", "Reddit"], timeout=30)
    assert len(rows) == 2
    assert rows[0]["exists"] == "claimed"


def test_sherlock_rows_no_csv_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.osint.username._require_binary", lambda name, hint: "/fake/bin/sherlock")

    def fake_run(argv: list[str], *, cwd: str, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr("app.osint.username._run", fake_run)
    with pytest.raises(AppOsintError, match="no CSV report"):
        _sherlock_rows("soxoj", sites=["GitHub"], timeout=30)


def test_add_sherlock_findings_filters_claimed_only() -> None:
    report = OsintReport(target="soxoj", target_type="username")
    rows = [
        {
            "name": "GitHub",
            "exists": "claimed",
            "url_user": "https://github.com/soxoj",
            "url_main": "https://github.com",
            "http_status": "200",
            "response_time_s": "1.2",
        },
        {
            "name": "Reddit",
            "exists": "not found",
            "url_user": "",
            "url_main": "",
            "http_status": "",
            "response_time_s": "",
        },
        {"exists": "claimed", "url_user": "", "url_main": "", "http_status": "", "response_time_s": ""},  # no site name
    ]
    assert _add_sherlock_findings(rows, "soxoj", report) == 1
    assert report.findings[0].source == "sherlock"
    assert report.findings[0].summary == "soxoj on GitHub"
    assert report.findings[0].data["http_status"] == "200"
    assert report.findings[0].data["response_time_s"] == "1.2"


# ── lookup_username ─────────────────────────────────────────────────────────


def test_lookup_username_sherlock_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    argv_calls = _install_sherlock_cli(
        monkeypatch,
        [
            {
                "username": "soxoj",
                "name": "GitHub",
                "url_main": "https://github.com",
                "url_user": "https://github.com/soxoj",
                "exists": "claimed",
                "http_status": "200",
                "response_time_s": "1.0",
            }
        ],
    )
    report = lookup_username("soxoj", timeout=30, sites=["GitHub"])

    assert report.target == "soxoj"
    assert report.target_type == "username"
    assert report.errors == []
    assert any(f.source == "sherlock" for f in report.findings)
    assert any(f.data["site"] == "GitHub" for f in report.findings)
    assert "--site" in argv_calls[0]
    assert "GitHub" in argv_calls[0]


def test_lookup_username_default_sites_used(monkeypatch: pytest.MonkeyPatch) -> None:
    argv_calls = _install_sherlock_cli(
        monkeypatch,
        [
            {
                "username": "soxoj",
                "name": "GitHub",
                "url_main": "https://github.com",
                "url_user": "https://github.com/soxoj",
                "exists": "claimed",
                "http_status": "200",
                "response_time_s": "1.0",
            }
        ],
    )
    report = lookup_username("soxoj", timeout=30)
    assert argv_calls[0].count("--site") == len(DEFAULT_SITES)
    assert any(f.source == "sherlock" for f in report.findings)  # primary path, no fallback


def test_lookup_username_empty_sites_full_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    argv_calls = _install_sherlock_cli(
        monkeypatch,
        [
            {
                "username": "soxoj",
                "name": "GitHub",
                "url_main": "https://github.com",
                "url_user": "https://github.com/soxoj",
                "exists": "claimed",
                "http_status": "200",
                "response_time_s": "1.0",
            }
        ],
    )
    report = lookup_username("soxoj", timeout=30, sites=[])
    assert "--site" not in argv_calls[0]
    assert any(f.source == "sherlock" for f in report.findings)


def test_lookup_username_sherlock_missing_falls_back_to_maigret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.osint.username._require_binary",
        lambda name, hint: (_ for _ in ()).throw(AppOsintNotAvailable("'sherlock' CLI not found")),
    )
    result = OsintResult(
        username="soxoj",
        sites=[
            OsintSite(site="GitHub", url_main="https://github.com", url_user="https://github.com/soxoj", exists=True)
        ],
        raw={},
        tool="maigret",
    )
    _install_maigret(monkeypatch, result=result)

    report = lookup_username("soxoj", timeout=30)
    assert any("CLI not found" in e for e in report.errors)
    assert any(f.source == "maigret" for f in report.findings)
    assert any(f.data["site"] == "GitHub" for f in report.findings)


def test_lookup_username_sherlock_error_falls_back_to_maigret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.osint.username._require_binary", lambda name, hint: "/fake/bin/sherlock")

    def fake_run(argv: list[str], *, cwd: str, timeout: int) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="timeout")

    monkeypatch.setattr("app.osint.username._run", fake_run)
    result = OsintResult(
        username="soxoj",
        sites=[
            OsintSite(site="Twitter", url_main="https://twitter.com", url_user="https://twitter.com/soxoj", exists=True)
        ],
        raw={},
        tool="maigret",
    )
    _install_maigret(monkeypatch, result=result)

    report = lookup_username("soxoj", timeout=30)
    assert any("sherlock: " in e for e in report.errors)
    assert any(f.source == "maigret" for f in report.findings)


def test_lookup_username_both_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.osint.username._require_binary",
        lambda name, hint: (_ for _ in ()).throw(AppOsintNotAvailable("'sherlock' CLI not found")),
    )
    _install_maigret(monkeypatch, exc=MaigretNotAvailable("'maigret' CLI not found"))
    with pytest.raises(AppOsintNotAvailable, match="requires an installed engine"):
        lookup_username("soxoj", timeout=30)


def test_lookup_username_maigret_error_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.osint.username._require_binary",
        lambda name, hint: (_ for _ in ()).throw(AppOsintNotAvailable("'sherlock' CLI not found")),
    )
    _install_maigret(monkeypatch, exc=MaigretError("maigret exploded"))
    report = lookup_username("soxoj", timeout=30)
    assert any("maigret: maigret exploded" in e for e in report.errors)
    assert report.findings == []


def test_add_maigret_findings() -> None:
    result = OsintResult(
        username="soxoj",
        sites=[
            OsintSite(site="GitHub", url_main="https://github.com", url_user="https://github.com/soxoj", exists=True),
            OsintSite(site="Reddit", url_main="https://reddit.com", url_user="", exists=False),
        ],
        raw={},
        tool="maigret",
    )
    report = OsintReport(target="soxoj", target_type="username")
    assert _add_maigret_findings(result, "soxoj", report) == 1
    assert report.findings[0].source == "maigret"
    assert report.findings[0].summary == "soxoj on GitHub"
