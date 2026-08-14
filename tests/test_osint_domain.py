"""Unit tests for ``app.osint.domain`` — all network sources mocked.

dnspython, python-whois, the ``whois`` CLI, ``ssl``/``socket`` and every httpx
call site are monkeypatched with in-memory fakes — nothing touches the network.
The optional-dependency flags (``_HTTPX_AVAILABLE`` / ``_DNS_AVAILABLE`` /
``_WHOIS_AVAILABLE``) are module-level booleans that tests flip to exercise
the import-safe degradation paths.

Covers:

* ``normalize_domain`` (scheme/path/port stripping, IDNA, IP rejection);
* per-source parsing: DNS records, WHOIS (python-whois dict + CLI text), SSL
  certificate, crt.sh subdomains, Wayback CDX, HTTP fingerprint;
* a full ``lookup_domain`` flow with every source returning data;
* graceful degradation when sources/dependencies are missing or failing.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

import app.osint.domain as domain
from app.osint.domain import normalize_domain

# ── normalize_domain ────────────────────────────────────────────────────────


def test_normalize_domain_strips_scheme_path_port() -> None:
    assert normalize_domain("https://Scam-Example.COM/order?id=1") == "scam-example.com"
    assert normalize_domain("http://example.com:8080/path") == "example.com"
    assert normalize_domain(" example.com. ") == "example.com"
    assert normalize_domain("子域名.example") == "xn--eqrt2grvd.example"


def test_normalize_domain_rejects_garbage() -> None:
    for bad in ("", "192.168.0.1", "not a domain", "http://", "-bad.example", "a" * 300):
        with pytest.raises(ValueError, match=r"(empty domain|expected a domain name|invalid domain name)"):
            normalize_domain(bad)


# ── Fake httpx client (routed by URL) ───────────────────────────────────────


class _FakeResponse:
    def __init__(
        self,
        payload: Any,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        url: str = "http://example.com/",
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.url = _FakeURL(url)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class _FakeURL:
    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value

    @property
    def host(self) -> str:
        return self.value.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]


class _FakeClient:
    """Routes ``get()`` to scripted payloads; records calls."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes  # URL-substring -> _FakeResponse or Exception
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def get(self, url: str, params: dict[str, Any] | None = None, **kwargs: Any) -> _FakeResponse:
        self.calls.append((url, params))
        for needle, resp in self.routes.items():
            if needle in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise RuntimeError(f"no fake route for {url}")


@pytest.fixture
def fake_httpx(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    """Install a scripted ``httpx.Client`` for the module's inner imports."""
    import httpx

    client = _FakeClient({})
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: client)
    return client


@pytest.fixture
def fake_dns(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install a scripted ``dns.resolver.Resolver``."""
    import dns.exception
    import dns.resolver

    state: dict[str, Any] = {"answers": {}, "nxdomain": False, "fail_types": set()}

    class FakeResolver:
        timeout = 1
        lifetime = 1

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def resolve(self, qname: str, rtype: str) -> list[str]:
            if state["nxdomain"]:
                raise dns.resolver.NXDOMAIN()
            if rtype in state["fail_types"]:
                raise dns.exception.Timeout()
            records = state["answers"].get(rtype, [])
            if not records:
                raise dns.resolver.NoAnswer()
            return list(records)

    monkeypatch.setattr(dns.resolver, "Resolver", FakeResolver)
    return state


@pytest.fixture
def fake_whois_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake ``whois`` CLI binary + capture the subprocess argv."""
    monkeypatch.setattr(domain, "_WHOIS_AVAILABLE", False)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/whois" if name == "whois" else None)

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(domain.subprocess, "run", fake_run)


# ── _parse_whois_cli ────────────────────────────────────────────────────────


def test_parse_whois_cli() -> None:
    text = (
        "   Domain Name: EXAMPLE.COM\n"
        "   Registrar: Example Registrar LLC\n"
        "   Creation Date: 2000-01-01T00:00:00Z\n"
        "   Registry Expiry Date: 2030-01-01T00:00:00Z\n"
        "   Registrant Organization: Example Corp\n"
        "   Registrant Country: US\n"
        "   Name Server: NS1.EXAMPLE.COM\n"
        "   Name Server: NS2.EXAMPLE.COM\n"
        "   Status: clientTransferProhibited\n"
    )
    parsed = domain._parse_whois_cli(text)
    assert parsed["registrar"] == "Example Registrar LLC"
    assert parsed["org"] == "Example Corp"
    assert parsed["country"] == "US"
    assert parsed["name_servers"] == ["NS1.EXAMPLE.COM", "NS2.EXAMPLE.COM"]
    assert parsed["status"] == ["clientTransferProhibited"]


# ── _extract_subdomains / _wayback_iso / _fmt_whois_dates ──────────────────


def test_extract_subdomains_deduplicates_and_filters() -> None:
    entries = [
        {"name_value": "example.com\nwww.example.com\n*.example.com"},
        {"common_name": "OTHER.com"},
        {"name_value": "api.Example.COM."},
        "not-a-dict",
    ]
    assert domain._extract_subdomains("example.com", entries) == ["api.example.com", "example.com", "www.example.com"]


def test_wayback_iso() -> None:
    assert domain._wayback_iso("20240101120000") == "2024-01-01T12:00:00Z"
    assert domain._wayback_iso("2024") == "2024"


def test_fmt_whois_dates() -> None:
    assert domain._fmt_whois_dates(None) is None
    assert domain._fmt_whois_dates(datetime(2020, 1, 2, 3, 4, 5)) == "2020-01-02T03:04:05"
    assert domain._fmt_whois_dates(["2020", None]) == ["2020"]


# ── Full lookup with every source mocked ────────────────────────────────────


def test_lookup_domain_full_flow(
    fake_httpx: _FakeClient, fake_dns: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dns["answers"] = {
        "A": ["93.184.216.34"],
        "MX": ["10 mail.example.com."],
        "NS": ["ns1.example.com."],
        "TXT": ['"v=spf1 -all"'],
    }
    fake_httpx.routes = {
        "crt.sh": _FakeResponse([{"name_value": "www.example.com"}], url="https://crt.sh/?q=%25.example.com"),
        "web.archive.org": _FakeResponse(
            [
                ["urlkey", "timestamp", "original"],
                ["example.com", "20240101120000", "http://example.com/"],
                ["example.com", "20250101120000", "http://example.com/"],
            ],
            url="http://web.archive.org/cdx/search/cdx",
        ),
        "https://example.com/": _FakeResponse(
            {"ok": True},
            status_code=200,
            headers={"server": "nginx", "x-powered-by": "Express"},
            url="https://example.com/",
        ),
    }

    monkeypatch.setattr(
        domain,
        "_python_whois",
        lambda d, t: {"org": "Example Corp", "registrar": "Example Registrar", "creation_date": datetime(2000, 1, 1)},
    )
    monkeypatch.setattr(
        domain,
        "_ssl_get_cert",
        lambda d, t, verify=True: {
            "issuer": ((("organizationName", "Example CA"),), (("commonName", "ca.example"),)),
            "subjectAltName": (("DNS", "example.com"), ("DNS", "www.example.com")),
            "notBefore": "Jan  1 00:00:00 2024 GMT",
            "notAfter": "Jan  1 00:00:00 2025 GMT",
        },
    )

    report = domain.lookup_domain("example.com", timeout=10)

    assert report.target == "example.com"
    assert report.target_type == "domain"
    assert report.errors == []
    categories = {f.category for f in report.findings}
    assert categories >= {"dns", "registrant", "registration", "certificate", "subdomain", "web-archive", "fingerprint"}

    dns_findings = [f for f in report.findings if f.source == "dns"]
    assert any(f.data.get("record_type") == "A" and "93.184.216.34" in f.data["records"] for f in dns_findings)
    assert any(f.summary == "example.com does not resolve (NXDOMAIN)" for f in report.findings) is False

    crt_finding = next(f for f in report.findings if f.source == "crt.sh")
    assert crt_finding.data["subdomains"] == ["www.example.com"]

    archive = next(f for f in report.findings if f.source == "web.archive.org")
    assert archive.data["snapshot_count"] == 2
    assert archive.data["first_snapshot"] == "2024-01-01T12:00:00Z"

    fp = next(f for f in report.findings if f.source == "http")
    assert fp.data["status_code"] == 200
    assert fp.data["headers"]["server"] == "nginx"

    ssl = next(f for f in report.findings if f.source == "ssl")
    assert ssl.data["subject_alt_names"] == ["DNS:example.com", "DNS:www.example.com"]


def test_lookup_domain_dns_nxdomain(fake_dns: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns["nxdomain"] = True
    # Degrade the remaining sources so only DNS runs.
    monkeypatch.setattr(domain, "_HTTPX_AVAILABLE", False)
    monkeypatch.setattr(domain, "_WHOIS_AVAILABLE", False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(domain, "_ssl_get_cert", lambda d, t, verify=True: (_ for _ in ()).throw(OSError("no tls")))
    report = domain.lookup_domain("nonexistent.example", timeout=5)
    assert any(f.summary == "nonexistent.example does not resolve (NXDOMAIN)" for f in report.findings)


def test_lookup_domain_dns_timeout_records_error(fake_dns: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    fake_dns["fail_types"] = {"A"}
    fake_dns["answers"] = {"MX": ["10 mail.example.com."]}
    monkeypatch.setattr(domain, "_HTTPX_AVAILABLE", False)
    monkeypatch.setattr(domain, "_WHOIS_AVAILABLE", False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(domain, "_ssl_get_cert", lambda d, t, verify=True: (_ for _ in ()).throw(OSError("no tls")))
    report = domain.lookup_domain("example.com", timeout=5)
    assert any("dns: A query timed out" in e for e in report.errors)
    assert any(f.category == "dns" for f in report.findings)


# ── WHOIS paths ─────────────────────────────────────────────────────────────


def test_whois_python_whois_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(domain, "_WHOIS_AVAILABLE", True)
    monkeypatch.setattr(
        domain,
        "_python_whois",
        lambda d, t: {
            "org": "Example Corp",
            "registrar": "Example Registrar",
            "creation_date": datetime(2000, 1, 1),
            "expiration_date": "2030-01-01",
            "emails": ["admin@example.com"],
        },
    )
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._whois_lookup("example.com", 5, report)
    assert report.errors == []
    assert any(f.category == "registrant" for f in report.findings)
    assert any(f.category == "registration" for f in report.findings)


def test_whois_python_whois_failure_falls_back_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(domain, "_WHOIS_AVAILABLE", True)
    monkeypatch.setattr(domain, "_python_whois", lambda d, t: (_ for _ in ()).throw(RuntimeError("whois lib boom")))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/whois" if name == "whois" else None)

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout="   Registrar: Fallback Registrar LLC\n   Registrant Organization: Fallback Corp\n",
            stderr="",
        )

    monkeypatch.setattr(domain.subprocess, "run", fake_run)
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._whois_lookup("example.com", 5, report)
    assert any("python-whois lookup failed" in e for e in report.errors)  # failure recorded
    assert any("Fallback Registrar LLC" in f.summary for f in report.findings)  # CLI fallback worked


def test_whois_neither_dependency_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(domain, "_WHOIS_AVAILABLE", False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._whois_lookup("example.com", 5, report)
    assert any("python-whois not installed" in e for e in report.errors)


# ── SSL paths ───────────────────────────────────────────────────────────────


def test_ssl_verification_failure_surfaces_and_reads_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    import ssl

    calls: list[bool] = []

    def fake_get_cert(domain: str, timeout: int, *, verify: bool) -> dict[str, Any]:
        calls.append(verify)
        if verify:
            raise ssl.SSLCertVerificationError("self-signed certificate")
        return {
            "issuer": ((("organizationName", "Self Signed CA"),),),
            "subjectAltName": (),
            "notBefore": "Jan  1 00:00:00 2024 GMT",
            "notAfter": "Jan  1 00:00:00 2025 GMT",
        }

    monkeypatch.setattr(domain, "_ssl_get_cert", fake_get_cert)
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._ssl_lookup("example.com", 5, report)
    assert calls == [True, False]
    assert any("does not verify" in f.summary for f in report.findings)
    assert any(f.source == "ssl" and "Self Signed CA" in f.summary for f in report.findings)


def test_ssl_handshake_failure_records_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        domain, "_ssl_get_cert", lambda d, t, verify=True: (_ for _ in ()).throw(OSError("connection refused"))
    )
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._ssl_lookup("example.com", 5, report)
    assert any("ssl: TLS handshake failed" in e for e in report.errors)


# ── crt.sh / Wayback / fingerprint ──────────────────────────────────────────


def test_crt_lookup_no_subdomains(fake_httpx: _FakeClient) -> None:
    fake_httpx.routes = {"crt.sh": _FakeResponse([])}
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._crt_lookup("example.com", 5, report)
    assert any("no certificate-transparency subdomains" in f.summary for f in report.findings)


def test_crt_lookup_http_failure_records_error(fake_httpx: _FakeClient) -> None:
    import httpx

    fake_httpx.routes = {"crt.sh": httpx.ConnectError("crt.sh down")}
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._crt_lookup("example.com", 5, report)
    assert any("crt.sh: query failed" in e for e in report.errors)


def test_wayback_no_snapshots(fake_httpx: _FakeClient) -> None:
    fake_httpx.routes = {"web.archive.org": _FakeResponse([["urlkey", "timestamp", "original"]])}
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._wayback_lookup("example.com", 5, report)
    assert any("no archived snapshots" in f.summary for f in report.findings)
    assert any(f.source == "web.archive.org" and f.data["snapshot_count"] == 0 for f in report.findings)


def test_fingerprint_https_then_http_fallback(fake_httpx: _FakeClient) -> None:
    import httpx

    fake_httpx.routes = {
        "https://example.com/": httpx.ConnectError("https refused"),
        "http://example.com/": _FakeResponse({"ok": True}, status_code=200, url="http://example.com/"),
    }
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._fingerprint_lookup("example.com", 5, report)
    fp = next(f for f in report.findings if f.source == "http")
    assert fp.data["status_code"] == 200
    assert fp.data["final_url"] == "http://example.com/"


def test_fingerprint_both_schemes_fail(fake_httpx: _FakeClient) -> None:
    import httpx

    fake_httpx.routes = {
        "https://example.com/": httpx.ConnectError("down"),
        "http://example.com/": httpx.ConnectError("down"),
    }
    report = domain.OsintReport(target="example.com", target_type="domain")
    domain._fingerprint_lookup("example.com", 5, report)
    assert any("fingerprint: HTTP(S) fetch failed" in e for e in report.errors)


# ── Graceful degradation ────────────────────────────────────────────────────


def test_lookup_domain_degrades_when_everything_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(domain, "_HTTPX_AVAILABLE", False)
    monkeypatch.setattr(domain, "_DNS_AVAILABLE", False)
    monkeypatch.setattr(domain, "_WHOIS_AVAILABLE", False)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(domain, "_ssl_get_cert", lambda d, t, verify=True: (_ for _ in ()).throw(OSError("no tls")))

    report = domain.lookup_domain("example.com", timeout=5)

    assert report.target == "example.com"
    assert report.findings == []
    assert any("dnspython not installed" in e for e in report.errors)
    assert any("python-whois not installed" in e for e in report.errors)
    assert any("httpx not installed" in e for e in report.errors)
    assert any("ssl: TLS handshake failed" in e for e in report.errors)


def test_lookup_domain_invalid_input_raises() -> None:
    with pytest.raises(ValueError, match="expected a domain name"):
        domain.lookup_domain("192.168.0.1")
    with pytest.raises(ValueError, match="empty domain"):
        domain.lookup_domain("")
    with pytest.raises(ValueError, match="timeout must be >= 1"):
        domain.lookup_domain("example.com", timeout=0)
