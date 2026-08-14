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
* **theHarvester** — passive email / hostname harvesting from public search
  engines (``theHarvester`` CLI; optional — skipped when not installed).
* **Amass** — passive DNS/subdomain mapping plus address / ASN attribution
  (``amass enum -passive``; optional — skipped when not installed).

Contract (see :mod:`app.osint`): ``lookup_domain(domain, *, timeout=60) ->
OsintReport``.

Error handling — import-safe, per-source degradation:
  * Missing optional dependency (``httpx``, ``dnspython``, ``python-whois``)
    -> a line in ``report.errors``; the module still imports and the other
    sources still run.
  * A failed source (timeout, network error, parse error) -> a line in
    ``report.errors``; the lookup never raises for a source failure.
  * Optional CLI tools (``theHarvester``, ``amass``) missing or failing ->
    a line logged via :mod:`logging`; the source is skipped, never raises,
    and never touches ``report.errors`` (WHOIS/DNS/SSL still produce a
    complete report).
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

import contextlib
import importlib.util
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
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

#: theHarvester search sources (comma-separated, no API keys required; see ``theHarvester -h``).
#: Chosen for speed: the 5-source default finished in ~60s while these three
#: complete in ~7s, which fits the shared lookup budget. Add keyless sources
#: (e.g. ``hudsonrock``) if slower, richer harvests are acceptable.
_THEHARVESTER_SOURCES: str = "crtsh,rapiddns,hackertarget"

#: Per-source result cap passed to theHarvester's ``--limit``.
_THEHARVESTER_LIMIT: int = 50

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


# ─── theHarvester (emails / hosts / subdomains) ─────────────────────────


def _theharvester_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Harvest emails + hostnames via the ``theHarvester`` CLI (passive search).

    Runs ``theHarvester -d <domain> -b <sources> -l <limit> -q -f <prefix>``,
    which writes a structured ``<prefix>.json`` (plus an XML copy) that is
    parsed preferentially; the plain-text stdout sections (``[*] Emails
    found: N`` / ``[*] Hosts found: N``) are parsed as a fallback for
    versions or runs where the file is not written.

    Optional source: the binary is resolved with :func:`shutil.which` and any
    absence/failure is logged and skipped — it never raises and never touches
    ``report.errors``, so WHOIS/DNS/SSL still produce a complete report.
    """
    bin_path = shutil.which("theHarvester")
    if not bin_path:
        logger.error("theharvester: binary not found (install with: pip install theHarvester) - skipping")
        return

    fd, json_path = tempfile.mkstemp(prefix="theharvester-", suffix=".json")
    os.close(fd)
    prefix = json_path[: -len(".json")]
    try:
        try:
            # Fixed argv list (no shell); domain is regex-validated upstream and
            # passed as a single element — S603 is a false positive.
            result = subprocess.run(  # noqa: S603
                [
                    bin_path,
                    "-d",
                    domain,
                    "-b",
                    _THEHARVESTER_SOURCES,
                    "-l",
                    str(_THEHARVESTER_LIMIT),
                    "-q",
                    "-f",
                    prefix,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("theharvester: timed out after %ss", timeout)
            return
        except Exception as exc:
            logger.error("theharvester: CLI run failed: %s", exc)
            return
        if result.returncode != 0:
            logger.warning("theharvester: exited with code %s; parsing partial output", result.returncode)
        emails, hosts = _parse_theharvester_output(domain, json_path, result.stdout or "")
    finally:
        for suffix in (".json", ".xml"):
            with contextlib.suppress(OSError):
                os.unlink(f"{prefix}{suffix}")
    _emit_theharvester(domain, report, emails, hosts)


def _parse_theharvester_output(domain: str, json_path: str, stdout: str) -> tuple[list[str], list[str]]:
    """Parse theHarvester output into ``(emails, hosts)`` lists.

    Prefers the structured ``<prefix>.json`` written by ``-f`` (robust across
    versions); falls back to the plain-text ``[*] Emails found: N`` /
    ``[*] Hosts found: N`` sections when the file is absent or unparsable.
    """
    data: Any = None
    try:
        with open(json_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = None
    if isinstance(data, dict):
        emails = [str(item) for item in (data.get("emails") or []) if item]
        hosts = [str(item) for item in (data.get("hosts") or []) if item]
        if emails or hosts:
            return _dedupe(emails), _dedupe(hosts)
    emails = _theharvester_section_items(stdout, "Emails found")
    hosts = _theharvester_section_items(stdout, "Hosts found")
    return _dedupe(emails), _dedupe(hosts)


def _theharvester_section_items(text: str, section: str) -> list[str]:
    """Extract the bullet items under a ``[*] <section>: N`` header.

    Tolerates the dashed separator line and blank lines; stops at the next
    ``[*]`` header or end of output.
    """
    pattern = re.compile(
        rf"^\[\*\]\s+{re.escape(section)}\s*:\s*\d+\s*\n(?:-+\s*\n)?(?P<body>.*?)(?=^\[\*\]|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return []
    items: list[str] = []
    for line in match.group("body").splitlines():
        line = line.strip()
        if not line or set(line) <= {"-"}:
            continue
        items.append(line)
    return items


def _classify_theharvester_hosts(hosts: list[str], domain: str) -> tuple[list[str], list[str]]:
    """Split theHarvester host entries into ``(subdomains, other-hosts)``.

    Entries are ``host`` or ``host:ip`` (theHarvester appends a resolved IP
    when a source resolves names; IPv6 addresses keep extra colons, so only
    the first segment matters). Names under ``domain`` count as subdomains;
    the bare domain, wildcards, and unrelated names count as hosts.
    """
    subdomains: set[str] = set()
    others: set[str] = set()
    suffix = f".{domain}"
    for entry in hosts:
        host = entry.split(":", 1)[0].strip().lower().rstrip(".")
        if not host:
            continue
        if host.startswith("*."):
            host = host[2:]
        if host == domain or host.endswith(suffix):
            subdomains.add(host)
        else:
            others.add(host)
    return sorted(subdomains), sorted(others)


def _dedupe(items: list[str]) -> list[str]:
    """Lowercase, dedupe, and sort a list of strings."""
    return sorted({item.strip().lower() for item in items if item.strip()})


def _emit_theharvester(domain: str, report: OsintReport, emails: list[str], hosts: list[str]) -> None:
    """Turn parsed theHarvester results into findings."""
    emails = sorted({email for email in emails if re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email)})
    subdomains, other_hosts = _classify_theharvester_hosts(hosts, domain)

    if emails:
        _finding(
            report,
            source="theharvester",
            category="email",
            summary=f"{len(emails)} email address(es) found by theHarvester",
            confidence="medium",
            emails=emails,
        )
    if subdomains:
        _finding(
            report,
            source="theharvester",
            category="subdomain",
            summary=f"{len(subdomains)} subdomain(s) found by theHarvester",
            confidence="medium",
            subdomains=subdomains,
        )
    if other_hosts:
        _finding(
            report,
            source="theharvester",
            category="host",
            summary=f"{len(other_hosts)} related host(s) found by theHarvester",
            confidence="medium",
            hosts=other_hosts,
        )
    if not (emails or subdomains or other_hosts):
        _finding(
            report,
            source="theharvester",
            category="recon",
            summary="theHarvester found no emails, subdomains, or hosts",
            confidence="medium",
        )


# ─── Amass (passive DNS / subdomain mapping) ──────────────────────────────


#: Cached result of sniffing whether the ``amass`` binary supports ``-json``
#: (amass v3 does; v4 dropped the flag in favour of ``-oA``).
_AMASS_JSON_SUPPORTED: bool | None = None

#: amass v4 ``-oA`` text output lines are graph edges: ``a --> TYPE --> b``.
_AMASS_EDGE_RE = re.compile(r"^(?P<left>.+?)\s+-->\s+(?P<type>[A-Za-z0-9_-]+)\s+-->\s+(?P<right>.+)$")

#: ANSI escape sequences amass may emit for colorized output.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _amass_lookup(domain: str, timeout: int, report: OsintReport) -> None:
    """Passive DNS/subdomain mapping via the ``amass`` CLI (OWASP Amass).

    Version-aware: amass v3 accepts ``enum -passive -d <domain> -json <file>``
    (JSON-Lines output); amass v4 removed ``-json`` and writes graph-edge text
    via ``enum -passive -nocolor -d <domain> -oA <prefix>`` instead. Both
    formats are parsed into subdomain / address (IP/CIDR) / ASN findings.

    Optional source: identical degradation semantics to theHarvester — a
    missing binary or failed run is logged and skipped, never raises, and
    never touches ``report.errors``.
    """
    bin_path = shutil.which("amass")
    if not bin_path:
        logger.error(
            "amass: binary not found (install with: go install github.com/owasp-amass/amass/v4/...@latest"
            " or apt install amass on Kali) - skipping"
        )
        return

    fd, out_path = tempfile.mkstemp(prefix="amass-", suffix=".json")
    os.close(fd)
    out_prefix = out_path[: -len(".json")]
    try:
        try:
            # Fixed argv list (no shell); domain is regex-validated upstream and
            # passed as a single element — S603 is a false positive.
            if _amass_supports_json(bin_path):
                argv = [bin_path, "enum", "-passive", "-d", domain, "-json", out_path]
            else:
                argv = [bin_path, "enum", "-passive", "-nocolor", "-d", domain, "-oA", out_prefix]
            result = subprocess.run(  # noqa: S603
                argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("amass: timed out after %ss", timeout)
            return
        except Exception as exc:
            logger.error("amass: CLI run failed: %s", exc)
            return
        if result.returncode != 0:
            logger.warning("amass: exited with code %s; parsing partial output", result.returncode)
        try:
            if _amass_supports_json(bin_path):
                entries = _read_amass_json(out_path)
            else:
                entries = _parse_amass_txt(_read_amass_txt(out_prefix + ".txt"), domain)
        except OSError as exc:
            logger.error("amass: could not read output: %s", exc)
            return
    finally:
        with contextlib.suppress(OSError):
            os.unlink(out_path)
        with contextlib.suppress(OSError):
            os.unlink(out_prefix + ".txt")
    _emit_amass(domain, report, entries)


def _amass_supports_json(bin_path: str) -> bool:
    """Return whether the ``amass`` binary still supports ``-json`` (v3 vs v4).

    Sniffed once per process from ``amass enum -h`` and cached; on any sniff
    failure we assume v4-style (``-oA`` is accepted by both v3 and v4).
    """
    global _AMASS_JSON_SUPPORTED
    if _AMASS_JSON_SUPPORTED is None:
        try:
            probe = subprocess.run(  # noqa: S603
                [bin_path, "enum", "-h"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                shell=False,
            )
            _AMASS_JSON_SUPPORTED = "-json" in (probe.stdout or "") or "-json" in (probe.stderr or "")
        except Exception as exc:
            logger.error("amass: could not probe version capabilities: %s", exc)
            _AMASS_JSON_SUPPORTED = False
    return _AMASS_JSON_SUPPORTED


def _read_amass_json(json_path: str) -> list[dict[str, Any]]:
    """Read amass ``-json`` output (one JSON object per line, invalid lines skipped)."""
    entries: list[dict[str, Any]] = []
    with open(json_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                entries.append(obj)
    return entries


def _read_amass_txt(txt_path: str) -> str:
    """Read amass ``-oA`` text output, stripping ANSI escape sequences."""
    with open(txt_path, encoding="utf-8", errors="replace") as handle:
        return _ANSI_RE.sub("", handle.read())


def _parse_amass_txt(text: str, domain: str) -> list[dict[str, Any]]:
    """Parse amass v4 ``-oA`` text output (graph-edge lines) into normalized entries.

    Each line is ``left --> TYPE --> right`` where either side may carry an
    ``(AssetType)`` suffix (``FQDN`` / ``IPAddress`` / ``Netblock`` / ``ASN``);
    bare lines are tolerated. Normalized entries reuse the v3 JSON-Lines keys
    so both paths share ``_emit_amass``.
    """
    entries: list[dict[str, Any]] = []
    suffix = f".{domain}"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = _AMASS_EDGE_RE.match(line)
        segments = [match.group("left"), match.group("right")] if match else [line]
        for segment in segments:
            name, asset_type = _split_amass_asset(segment)
            if not name:
                continue
            if asset_type == "IPAddress" or _looks_like_ip(name):
                entries.append({"name": name, "addresses": [{"ip": name, "cidr": ""}]})
            elif asset_type == "Netblock" or _looks_like_cidr(name):
                entries.append({"addresses": [{"ip": "", "cidr": name}]})
            elif asset_type == "ASN" or (name.isdigit() and len(name) <= 10):
                entries.append({"asn": name})
            elif name == domain or name.endswith(suffix):
                entries.append({"name": name})
    return entries


def _split_amass_asset(segment: str) -> tuple[str, str]:
    """Split ``name (AssetType)`` into ``(name, AssetType)``; tolerate bare names."""
    segment = segment.strip()
    if " (" in segment and segment.endswith(")"):
        name, _, asset_type = segment.rpartition("(")
        return name.strip().lower().rstrip("."), asset_type.rstrip(")").strip()
    return segment.strip().lower().rstrip("."), ""


def _looks_like_ip(value: str) -> bool:
    """Heuristic: bare IPv4/IPv6 literal (no DNS lookup needed)."""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _looks_like_cidr(value: str) -> bool:
    """Heuristic: bare CIDR block like ``93.184.216.0/24``."""
    return "/" in value and not value.endswith(".")


def _emit_amass(domain: str, report: OsintReport, entries: list[dict[str, Any]]) -> None:
    """Turn amass entries (JSON-Lines or normalized text) into findings."""
    subdomains: set[str] = set()
    addresses: set[tuple[str, str]] = set()  # (ip, cidr)
    asns: set[tuple[str, str]] = set()  # (asn, description)
    suffix = f".{domain}"

    for entry in entries:
        name = entry.get("name")
        if isinstance(name, str):
            name = name.strip().lower().rstrip(".")
            if name.startswith("*."):
                name = name[2:]
            if name == domain or name.endswith(suffix):
                subdomains.add(name)
        for addr in entry.get("addresses") or []:
            if isinstance(addr, dict):
                ip = addr.get("ip")
                cidr = addr.get("cidr")
                if isinstance(ip, str) and isinstance(cidr, str) and (ip.strip() or cidr.strip()):
                    addresses.add((ip.strip(), cidr.strip()))
        asn = entry.get("asn")
        desc = entry.get("desc")
        if asn is not None:
            asns.add((str(asn), str(desc) if isinstance(desc, str) and desc.strip() else ""))

    if subdomains:
        _finding(
            report,
            source="amass",
            category="subdomain",
            summary=f"{len(subdomains)} subdomain(s) discovered by Amass passive enumeration",
            confidence="medium",
            subdomains=sorted(subdomains),
        )
    if addresses:
        _finding(
            report,
            source="amass",
            category="address",
            summary=f"{len(addresses)} address(es) resolved by Amass",
            confidence="medium",
            addresses=[{"ip": ip, "cidr": cidr} for ip, cidr in sorted(addresses)],
        )
    if asns:
        _finding(
            report,
            source="amass",
            category="asn",
            summary=f"{len(asns)} ASN(s) associated with {domain} by Amass",
            confidence="medium",
            asns=[{"asn": asn, "desc": desc} for asn, desc in sorted(asns)],
        )
    if not (subdomains or addresses or asns):
        _finding(
            report,
            source="amass",
            category="recon",
            summary="Amass passive enumeration found no subdomains, addresses, or ASNs",
            confidence="medium",
        )


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
    """Investigate a domain across WHOIS, DNS, subdomains, SSL, and archive,
    plus the optional theHarvester and Amass CLI harvesters.

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
    logger.debug("domain OSINT lookup for %s (timeout=%ss)", target, timeout)

    deadline = time.monotonic() + timeout

    def _budget() -> int:
        return max(1, int(deadline - time.monotonic()))

    _dns_lookup(target, _budget(), report)
    _whois_lookup(target, _budget(), report)
    _ssl_lookup(target, _budget(), report)
    _crt_lookup(target, _budget(), report)
    _wayback_lookup(target, _budget(), report)
    _fingerprint_lookup(target, _budget(), report)
    # Optional passive harvesters run last with whatever budget remains; a
    # missing binary or failure is logged and skipped (see module docstring).
    _theharvester_lookup(target, _budget(), report)
    _amass_lookup(target, _budget(), report)
    return report
