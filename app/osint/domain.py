"""Domain OSINT investigation — WHOIS / DNS / subdomains / SSL / web archive.

Investigates a domain name (e.g. a scam landing page) across independent
sources and returns a unified :class:`app.osint.OsintReport`:

* **WHOIS** — registrant, registrar, creation/expiration dates, nameservers.
  Uses ``python-whois`` when installed, falling back to the ``whois`` CLI.
* **DNS** — A / AAAA / MX / NS / TXT / CNAME records via ``dnspython``.
* **Subdomains** — certificate-transparency names from the crt.sh JSON API
  (``https://crt.sh/?q=%25.<domain>&output=json``).
* **SSL certificate** — issuer, validity window, subject alternative names
  (stdlib ``ssl`` + ``socket``; the SANs often list *related* scam domains).
* **Web archive** — first/last Wayback Machine snapshot and capture count
  (Wayback CDX API).
* **Tech fingerprint** — HTTP status, final URL, ``Server`` and security
  headers via ``httpx``.

Contract (see :mod:`app.osint`): ``lookup_domain(domain, *, timeout=60) ->
OsintReport``.

Error handling — import-safe, per-source degradation:
  * Missing optional dependency (``httpx``, ``dnspython``, ``python-whois``)
    -> a line in ``report.errors``; the module still imports and the other
    sources still run.
  * A failed source (timeout, network error, parse error) -> a line in
    ``report.errors``; the lookup never raises for a source failure.
  * Garbage input (scheme/path/port/IP, unparsable hostname) -> ``ValueError``
    before any network I/O.

Usage::

    from app.osint.domain import lookup_domain

    report = lookup_domain("https://scam-example.com/order", timeout=45)
    print(report.found, report.errors)
    for finding in report.findings:
        print(finding.source, finding.category, finding.summary, finding.confidence)
"""

from __future__ import annotations

import importlib.util
import ipaddress
import logging
import re
import shutil
import socket
import ssl
import subprocess
import time
from datetime import datetime
from typing import Any

from app.osint import OsintFinding, OsintReport

logger = logging.getLogger(__name__)

__all__ = ["lookup_domain"]

# ─── Tunables ────────────────────────────────────────────────────────

#: User-Agent used for the HTTP sources (crt.sh, Wayback, fingerprinting).
_HTTP_HEADERS: dict[str, str] = {"User-Agent": "Mozilla/5.0 (compatible; RugMunch-OSINT/1.0; +https://rugmunch.io)"}

#: Domain pattern: labels of 1-63 chars, at least two labels, TLD of 2-63
#: letters, total length <= 253 (RFC 1035/1123, applied post-IDNA).
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

#: Record types queried by the DNS source.
_DNS_RECORD_TYPES: tuple[str, ...] = ("A", "AAAA", "MX", "NS", "TXT", "CNAME")

#: HTTP headers of interest for the tech-fingerprint source (lowercase).
_FINGERPRINT_HEADERS: frozenset[str] = frozenset(
    {
        "server",
        "x-powered-by",
        "x-aspnet-version",
        "content-type",
        "content-language",
        "x-frame-options",
        "strict-transport-security",
    }
)

#: Upper bound of crt.sh rows processed (popular domains return huge JSON).
_CRT_ROW_LIMIT: int = 5000

# ─── Optional dependency detection (import-safe, per-source degradation) ────
#: Whether httpx is importable (crt.sh / Wayback / fingerprint sources).
_HTTPX_AVAILABLE: bool = importlib.util.find_spec("httpx") is not None
#: Whether dnspython is importable (DNS source).
_DNS_AVAILABLE: bool = importlib.util.find_spec("dns") is not None
#: Whether python-whois is importable (WHOIS source; CLI fallback otherwise).
_WHOIS_AVAILABLE: bool = importlib.util.find_spec("whois") is not None


# ─── Input normalization ─────────────────────────────────────────────


def normalize_domain(value: str) -> str:
    """Normalize and validate a user-supplied domain.

    Strips scheme, path, query, fragment, port, and trailing dot; lowercases;
    IDNA-encodes non-ASCII labels. Raises :class:`ValueError` on garbage.

    Examples:
        ``"https://Scam-Example.COM/order?id=1"`` -> ``"scam-example.com"``
        ``"192.168.0.1"`` or ``"not a domain"`` -> :class:`ValueError`
    """
    raw = value.strip()
    if not raw:
        raise ValueError("empty domain")
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    for sep in ("/", "?", "#"):
        raw = raw.split(sep, 1)[0]
    if raw.count(":") == 1 and raw.rsplit(":", 1)[1].isdigit():
        raw = raw.rsplit(":", 1)[0]
    raw = raw.strip().rstrip(".")

    try:
        ipaddress.ip_address(raw)
    except ValueError:
        pass
    else:
        raise ValueError(f"expected a domain name, got an IP address: {raw!r}")

    try:
        ascii_domain = raw.lower().encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError(f"invalid domain name: {raw!r}") from exc

    if not _DOMAIN_RE.match(ascii_domain) or len(ascii_domain) > 253:
        raise ValueError(f"invalid domain name: {raw!r}")
    return ascii_domain


# ─── Finding emission ────────────────────────────────────────────────


def _finding(
    report: OsintReport,
    *,
    source: str,
    category: str,
    summary: str,
    confidence: str = "medium",
    **data: Any,
) -> None:
    """Map a resolved fact to an :class:`OsintFinding` and append it."""
    report.findings.append(
        OsintFinding(source=source, category=category, summary=summary, confidence=confidence, data=dict(data))
    )


# ─── DNS ─────────────────────────────────────────────────────────────


def _dns_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Resolve A/AAAA/MX/NS/TXT/CNAME records via dnspython."""
    if not _DNS_AVAILABLE:
        report.errors.append("dns: dnspython not installed (pip install dnspython)")
        return
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        report.errors.append("dns: dnspython not installed (pip install dnspython)")
        return

    resolver = dns.resolver.Resolver(configure=True)
    resolver.timeout = timeout
    resolver.lifetime = timeout

    for rtype in _DNS_RECORD_TYPES:
        try:
            answers = resolver.resolve(domain, rtype)
        except dns.resolver.NXDOMAIN:
            _finding(
                report,
                source="dns",
                category="dns",
                summary=f"{domain} does not resolve (NXDOMAIN)",
                confidence="high",
            )
            return
        except dns.resolver.NoAnswer:
            continue  # name exists but has no records of this type — normal
        except dns.exception.Timeout:
            report.errors.append(f"dns: {rtype} query timed out")
            continue
        except Exception as exc:  # NoNameservers, network, etc.
            report.errors.append(f"dns: {rtype} lookup failed: {exc}")
            continue

        records = [str(rdata) for rdata in answers]
        if records:
            _finding(
                report,
                source="dns",
                category="dns",
                summary=f"{len(records)} {rtype} record(s)",
                confidence="high",
                record_type=rtype,
                records=records,
            )


# ─── WHOIS ───────────────────────────────────────────────────────────


def _whois_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Registrant + registration details via python-whois, else the whois CLI."""
    if _WHOIS_AVAILABLE:
        try:
            entry = _python_whois(domain, timeout)
        except Exception as exc:
            report.errors.append(f"whois: python-whois lookup failed: {exc}")
            entry = None
        if entry:
            _emit_whois(report, entry)
            return

    whois_bin = shutil.which("whois")
    if whois_bin:
        try:
            # Fixed argv list (no shell); domain is regex-validated and passed as
            # a single element, so no injection surface — S603 is a false positive.
            result = subprocess.run(  # noqa: S603
                [whois_bin, domain],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except Exception as exc:
            report.errors.append(f"whois: CLI lookup failed: {exc}")
            return
        parsed = _parse_whois_cli(result.stdout or "")
        if not parsed:
            report.errors.append("whois: CLI produced no parseable output")
            return
        _emit_whois(report, parsed)
        return

    report.errors.append("whois: python-whois not installed and no whois CLI available (pip install python-whois)")


def _python_whois(domain: str, timeout: int) -> dict[str, Any]:
    """Run python-whois against the registrable domain (version-agnostic)."""
    import whois as whois_mod

    # python-whois 0.9.x exposes whois(); 0.8.x exposes query().
    func = getattr(whois_mod, "whois", None)
    if callable(func):
        entry = func(domain)
    else:
        query = getattr(whois_mod, "query", None)
        if callable(query):
            entry = query(domain, timeout=timeout)
        else:
            raise RuntimeError("python-whois exposes neither whois() nor query()")
    if isinstance(entry, dict):
        return dict(entry)
    raise RuntimeError(f"unexpected python-whois result type: {type(entry).__name__}")


def _parse_whois_cli(text: str) -> dict[str, Any]:
    """Minimal text parse of the ``whois`` CLI output for key fields."""
    out: dict[str, Any] = {}
    patterns = {
        "registrar": r"^\s*Registrar:\s*(.+)$",
        "creation_date": r"^\s*Creation Date:\s*(.+)$",
        "expiration_date": r"^\s*(?:Registry Expiry Date|Expiration Date|Registrar Registration Expiration Date):\s*(.+)$",
        "updated_date": r"^\s*Updated Date:\s*(.+)$",
        "name": r"^\s*Registrant Name:\s*(.+)$",
        "org": r"^\s*Registrant Organization:\s*(.+)$",
        "country": r"^\s*Registrant Country:\s*(.+)$",
        "dnssec": r"^\s*DNSSEC:\s*(.+)$",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match and match.group(1).strip():
            out[key] = match.group(1).strip()
    nameservers = re.findall(r"^\s*Name Server:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if nameservers:
        out["name_servers"] = [ns.strip() for ns in nameservers]
    status = re.findall(r"^\s*Status:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if status:
        out["status"] = [s.strip() for s in status]
    return out


def _emit_whois(report: OsintReport, data: dict[str, Any]) -> None:
    """Turn a whois result (python-whois dict or CLI parse) into findings."""
    registrant: dict[str, Any] = {}
    for key in ("name", "org", "country", "address", "city", "state", "registrant_postal_code"):
        value = data.get(key)
        if value not in (None, ""):
            registrant[key] = value
    emails = data.get("emails")
    if emails:
        registrant["emails"] = emails if isinstance(emails, list) else [emails]
    if registrant:
        _finding(
            report,
            source="whois",
            category="registrant",
            summary="registrant information",
            confidence="medium",
            **registrant,
        )

    extra: dict[str, Any] = {}
    registrar = data.get("registrar")
    created = _fmt_whois_dates(data.get("creation_date"))
    expires = _fmt_whois_dates(data.get("expiration_date"))
    updated = _fmt_whois_dates(data.get("updated_date"))
    nameservers = data.get("name_servers")
    status = data.get("status")
    dnssec = data.get("dnssec")

    if registrar:
        extra["registrar"] = registrar
    if created:
        extra["creation_date"] = created
    if expires:
        extra["expiration_date"] = expires
    if updated:
        extra["updated_date"] = updated
    if nameservers:
        extra["name_servers"] = nameservers if isinstance(nameservers, list) else [str(nameservers)]
    if status:
        extra["status"] = status if isinstance(status, list) else [str(status)]
    if dnssec:
        extra["dnssec"] = dnssec
    if not extra:
        return

    bits = []
    if registrar:
        bits.append(f"registrar {registrar}")
    if created:
        bits.append(f"created {created}")
    if expires:
        bits.append(f"expires {expires}")
    _finding(
        report,
        source="whois",
        category="registration",
        summary="; ".join(bits) if bits else "registration details",
        confidence="high",
        **extra,
    )


def _fmt_whois_dates(value: Any) -> Any:
    """Normalize whois datetime values (single or list) to ISO strings."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        formatted = [_fmt_whois_dates(item) for item in value]
        return [item for item in formatted if item is not None] or None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


# ─── SSL certificate ─────────────────────────────────────────────────


def _ssl_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Grab the TLS certificate issuer / validity / SANs on port 443."""
    try:
        cert = _ssl_get_cert(domain, timeout, verify=True)
    except ssl.SSLCertVerificationError as exc:
        # Self-signed / mismatched certs are common on scam sites — surface it.
        _finding(
            report,
            source="ssl",
            category="certificate",
            summary="TLS certificate does not verify (self-signed or invalid)",
            confidence="high",
            verify_error=str(exc),
        )
        try:
            cert = _ssl_get_cert(domain, timeout, verify=False)
        except Exception as exc2:
            report.errors.append(f"ssl: could not read certificate after verification failure: {exc2}")
            return
    except (ssl.SSLError, OSError) as exc:
        report.errors.append(f"ssl: TLS handshake failed: {exc}")
        return

    issuer = _flatten_cert_attrs(cert.get("issuer"))
    sans = _flatten_san(cert.get("subjectAltName"))
    org = issuer.get("organizationName", "")
    cn = issuer.get("commonName", "")
    not_before = cert.get("notBefore")
    not_after = cert.get("notAfter")

    data: dict[str, Any] = {"issuer": issuer, "not_before": not_before, "not_after": not_after}
    if sans:
        data["subject_alt_names"] = sans

    _finding(
        report,
        source="ssl",
        category="certificate",
        summary=f"TLS certificate issued by {org or cn or 'unknown'} (valid until {not_after or 'unknown'})",
        confidence="high",
        **data,
    )


def _ssl_get_cert(domain: str, timeout: int, *, verify: bool) -> dict[str, Any]:
    """Perform a TLS handshake and return the parsed peer certificate."""
    context = ssl.create_default_context() if verify else _unverified_context()
    with (
        socket.create_connection((domain, 443), timeout=timeout) as raw,
        context.wrap_socket(raw, server_hostname=domain) as tls,
    ):
        cert = tls.getpeercert()
    if not cert:
        raise ssl.SSLError("server presented no certificate")
    return cert


def _unverified_context() -> ssl.SSLContext:
    """TLS context that still *reads* the peer cert when verification fails."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _flatten_cert_attrs(items: Any) -> dict[str, str]:
    """Flatten ``getpeercert()`` RDN structure (issuer/subject) to name->value."""
    out: dict[str, str] = {}
    if not isinstance(items, (list, tuple)):
        return out
    for rdn in items:
        if not isinstance(rdn, (list, tuple)):
            continue
        for pair in rdn:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                key, value = pair
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    key, value = value
                if isinstance(key, str) and isinstance(value, str):
                    out[key] = value
    return out


def _flatten_san(items: Any) -> list[str]:
    """Flatten ``subjectAltName`` tuples into ``"TYPE:value"`` strings."""
    out: list[str] = []
    if not isinstance(items, (list, tuple)):
        return out
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            kind, value = item
            if isinstance(kind, str) and isinstance(value, str):
                out.append(f"{kind}:{value}")
    return out


# ─── Subdomains (crt.sh certificate transparency) ────────────────────


def _crt_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Query crt.sh for certificate-transparency subdomain names."""
    if not _HTTPX_AVAILABLE:
        report.errors.append("crt.sh: httpx not installed (pip install httpx)")
        return
    try:
        import httpx
    except ImportError:
        report.errors.append("crt.sh: httpx not installed (pip install httpx)")
        return

    params = {"q": f"%.{domain}", "output": "json"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client:
            response = client.get("https://crt.sh/", params=params)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        report.errors.append(f"crt.sh: query failed: {exc}")
        return

    if not isinstance(data, list):
        report.errors.append("crt.sh: unexpected response shape")
        return

    subdomains = _extract_subdomains(domain, data)
    if not subdomains:
        _finding(
            report,
            source="crt.sh",
            category="subdomain",
            summary=f"no certificate-transparency subdomains found for {domain}",
            confidence="medium",
        )
        return
    _finding(
        report,
        source="crt.sh",
        category="subdomain",
        summary=f"{len(subdomains)} subdomain(s) found in certificate-transparency logs",
        confidence="high",
        subdomains=subdomains,
    )


def _extract_subdomains(domain: str, entries: list[Any]) -> list[str]:
    """Deduplicate + sort crt.sh names that belong to ``domain``."""
    found: set[str] = set()
    suffix = f".{domain}"
    for entry in entries[:_CRT_ROW_LIMIT]:
        if not isinstance(entry, dict):
            continue
        name_value = entry.get("name_value") or entry.get("common_name") or ""
        for name in str(name_value).splitlines():
            name = name.strip().lower().rstrip(".")
            if name.startswith("*."):
                name = name[2:]
            if name == domain or name.endswith(suffix):
                found.add(name)
    return sorted(found)


# ─── Web archive (Wayback Machine CDX) ───────────────────────────────


def _wayback_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Query the Wayback CDX API for first/last snapshot and capture count."""
    if not _HTTPX_AVAILABLE:
        report.errors.append("web-archive: httpx not installed (pip install httpx)")
        return
    try:
        import httpx
    except ImportError:
        report.errors.append("web-archive: httpx not installed (pip install httpx)")
        return

    params = {"url": domain, "output": "json", "limit": "10"}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client:
            response = client.get("http://web.archive.org/cdx/search/cdx", params=params)
            response.raise_for_status()
            rows = response.json()
    except Exception as exc:
        report.errors.append(f"web-archive: CDX query failed: {exc}")
        return

    if not isinstance(rows, list) or not rows or not isinstance(rows[0], list):
        report.errors.append("web-archive: unexpected CDX response shape")
        return
    header = rows[0]
    try:
        timestamp_index = header.index("timestamp")
    except ValueError:
        report.errors.append("web-archive: CDX response missing timestamp column")
        return

    stamps = [
        row[timestamp_index]
        for row in rows[1:]
        if isinstance(row, list)
        and len(row) > timestamp_index
        and isinstance(row[timestamp_index], str)
        and row[timestamp_index].isdigit()
    ]
    if not stamps:
        _finding(
            report,
            source="web.archive.org",
            category="web-archive",
            summary=f"no archived snapshots found for {domain}",
            confidence="medium",
            snapshot_count=0,
            first_snapshot=None,
            last_snapshot=None,
        )
        return
    _finding(
        report,
        source="web.archive.org",
        category="web-archive",
        summary=f"{len(stamps)} archived snapshot(s); first {_wayback_iso(min(stamps))}, last {_wayback_iso(max(stamps))}",
        confidence="high",
        snapshot_count=len(stamps),
        first_snapshot=_wayback_iso(min(stamps)),
        last_snapshot=_wayback_iso(max(stamps)),
    )


def _wayback_iso(timestamp: str) -> str:
    """Convert a CDX ``YYYYMMDDHHMMSS`` timestamp to an ISO-8601 string."""
    if len(timestamp) == 14 and timestamp.isdigit():
        return f"{timestamp[0:4]}-{timestamp[4:6]}-{timestamp[6:8]}T{timestamp[8:10]}:{timestamp[10:12]}:{timestamp[12:14]}Z"
    return timestamp


# ─── Tech fingerprint (HTTP) ─────────────────────────────────────────


def _fingerprint_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Fetch the site and record status, final URL, and interesting headers."""
    if not _HTTPX_AVAILABLE:
        report.errors.append("fingerprint: httpx not installed (pip install httpx)")
        return
    try:
        import httpx
    except ImportError:
        report.errors.append("fingerprint: httpx not installed (pip install httpx)")
        return

    response = None
    last_error: Exception | None = None
    for scheme in ("https", "http"):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client:
                response = client.get(f"{scheme}://{domain}/")
            break
        except Exception as exc:
            last_error = exc

    if response is None:
        report.errors.append(f"fingerprint: HTTP(S) fetch failed: {last_error or 'unknown error'}")
        return

    picked: dict[str, str] = {}
    for key, value in response.headers.items():
        if key in _FINGERPRINT_HEADERS:
            picked[key] = value

    data: dict[str, Any] = {"status_code": response.status_code, "final_url": str(response.url)}
    if picked:
        data["headers"] = picked
    _finding(
        report,
        source="http",
        category="fingerprint",
        summary=f"HTTP {response.status_code} response, final host {response.url.host or domain}",
        confidence="medium",
        **data,
    )


# ─── Entry point ─────────────────────────────────────────────────────


def lookup_domain(domain: str, *, timeout: int = 60) -> OsintReport:
    """Investigate a domain across WHOIS, DNS, subdomains, SSL, and archive.

    Args:
        domain: Domain or URL to investigate (scheme/path/port are stripped).
        timeout: Total budget in seconds for the whole lookup, shared by all
            sources; each source gets the remaining budget when it runs.

    Returns:
        An :class:`OsintReport` with one finding per resolved fact and one
        error string per failed/missing source. Never raises for source
        failures; raises :class:`ValueError` only for invalid input.
    """
    if timeout < 1:
        raise ValueError("timeout must be >= 1")
    target = normalize_domain(domain)
    report = OsintReport(target=target, target_type="domain")

    deadline = time.monotonic() + timeout

    def _budget() -> int:
        return max(1, int(deadline - time.monotonic()))

    _dns_lookup(target, _budget(), report)
    _whois_lookup(target, _budget(), report)
    _ssl_lookup(target, _budget(), report)
    _crt_lookup(target, _budget(), report)
    _wayback_lookup(target, _budget(), report)
    _fingerprint_lookup(target, _budget(), report)
    return report
