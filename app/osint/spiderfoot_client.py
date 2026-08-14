"""SpiderFoot OSINT automation engine client.

Runs a scan against a self-hosted SpiderFoot instance (``infra/spiderfoot/``)
and returns a unified :class:`app.osint.OsintReport`.

SpiderFoot is the general OSINT backbone of the section: given a domain,
email, username, wallet or IP it fans out across 100+ modules (DNS/WHOIS,
certificate-transparency logs, breach feeds, social/profile enumeration,
blockchain, …) and we map its per-event results onto our report model.

API contract (OSS v4.0.0 — **not** the HX ``/rest/v1`` API, which is a paid
cloud feature; the self-hosted web UI exposes these JSON endpoints when the
request carries ``Accept: application/json``):

    POST /startscan         -> ["SUCCESS", scanId] | ["ERROR", msg]
    GET  /scanstatus?id=..  -> [name, target, created, started, ended, status, riskmatrix]
    GET  /scaneventresults?id=..&eventType=ALL
                           -> [[lastseen, data, source_data, module, confidence,
                               visibility, risk, hash, fp, parent_fp, event_type], ...]
    GET  /scanerrors?id=..  -> [error strings]

Scan lifecycle: ``INITIALIZING -> STARTING -> STARTED -> RUNNING`` and then a
terminal state ``FINISHED`` (success) or ``ERROR-FAILED`` / ``ABORTED``.

Environment:

    SPIDERFOOT_URL      base URL of the SpiderFoot instance
                        (default ``http://127.0.0.1:5001``)
    SPIDERFOOT_TOKEN    optional bearer token sent as ``Authorization: Bearer``
                        (forward-compat / reverse-proxy auth; the OSS web UI
                        itself has no token auth — keep it loopback-bound)
    SPIDERFOOT_USECASE  module group used for the scan (default ``Passive``;
                        the safe set — no active scanning of the target)

Error handling — graceful by design:

    * Server unreachable / connection refused / HTTP error at start
      -> raises :class:`app.osint.OsintNotAvailable`.
    * Scan ends in ERROR-FAILED / ABORTED, or the poll budget runs out
      -> the report is returned with a line in ``report.errors`` and any
      results already collected.
    * A module-level failure inside SpiderFoot (visible via ``/scanerrors``)
      -> appended to ``report.errors``; the rest of the scan is unaffected.

Contract (see :mod:`app.osint`): ``lookup(target, target_type="domain", *,
timeout=120) -> OsintReport``.

Usage::

    from app.osint.spiderfoot_client import lookup

    report = lookup("example.com", timeout=120)
    print(report.found, report.errors)
    for finding in report.findings:
        print(finding.source, finding.category, finding.summary, finding.confidence)
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from typing import Any

import httpx

from app.osint import OsintFinding, OsintNotAvailable, OsintReport

logger = logging.getLogger(__name__)

__all__ = ["lookup"]

# ─── Tunables ────────────────────────────────────────────────────────

#: Base URL of the self-hosted SpiderFoot instance.
DEFAULT_SPIDERFOOT_URL = "http://127.0.0.1:5001"

#: Module group used when the caller does not pin an explicit module list.
#: "Passive" = the safe default set (199 modules that only consult third-party
#: data sources; no active port scans / direct target interaction).
DEFAULT_USECASE = "Passive"

#: Scan statuses that mean "done" (success or failure).
_TERMINAL_STATUSES: frozenset[str] = frozenset({"FINISHED", "ERROR-FAILED", "ABORTED"})

#: Statuses the scan is still working in (poll again).
_RUNNING_STATUSES: frozenset[str] = frozenset({"INITIALIZING", "STARTING", "STARTED", "RUNNING"})

#: Poll interval while waiting for a scan to finish.
_POLL_INTERVAL_SECONDS: float = 5.0

#: Upper bound on the summary/data string length we put in a finding.
_MAX_FIELD_LENGTH: int = 5000

#: Seed/ROOT events (the target itself) are noise — skip them.
_SKIP_EVENT_TYPES: frozenset[str] = frozenset({"ROOT"})

#: Event types SpiderFoot never produces findings for even if seen.
_NOISE_EVENT_TYPES: frozenset[str] = frozenset({"WEBSERVER_HTTP_HEADERS"})

#: Mapping of interesting SpiderFoot event types to our report categories.
#: Falls back to a snake_case version of the SpiderFoot type.
_EVENT_CATEGORY: dict[str, str] = {
    "DOMAIN_NAME": "domain",
    "INTERNET_NAME": "domain",
    "IP_ADDRESS": "ip_address",
    "IPV6_ADDRESS": "ip_address",
    "NETBLOCK_OWNER": "netblock",
    "EMAILADDR": "email",
    "USERNAME": "username",
    "PHONE_NUMBER": "phone",
    "BITCOIN_ADDRESS": "bitcoin",
    "ETHEREUM_ADDRESS": "ethereum",
    "HUMAN_NAME": "person",
    "ORG_NAME": "organization",
    "AFFILIATE_INTERNET_NAME": "affiliate",
    "SUBDOMAIN": "subdomain",
    "WEBSERVER_TECHNOLOGY": "technology",
    "SSL_CERTIFICATE_ISSUED": "certificate",
    "SSL_CERTIFICATE_ISSUER": "certificate",
    "DNS_SERVER": "dns",
    "MAIL_SERVER": "dns",
    "NAMESERVER": "dns",
    "CO_HOSTED_SITE": "co_hosted",
    "PHYSICAL_ADDRESS": "address",
    "GEOINFO": "geolocation",
    "SOCIAL_MEDIA": "social",
    "SOCIAL_PROFILE": "social",
    "ACCOUNT": "account",
    "BREACHED_ACCOUNT": "breach",
    "LEAKED_ACCOUNT": "breach",
    "SANCTIONED_ENTITY": "sanction",
    "BLACKLISTED_IP": "blacklist",
    "VULNERABILITY": "vulnerability",
    "REPUTATION": "reputation",
    "HISTORIC_WHOIS": "whois",
    "DOMAIN_REGISTRAR": "whois",
    "DOMAIN_WHOIS": "whois",
    "OPERATING_SYSTEM": "technology",
    "PROVIDER_TELCO": "provider",
    "PROVIDER_HOSTING": "provider",
    "PROVIDER_DNS": "provider",
    "RAW_DATA": "raw",
    "SEARCH_ENGINE_WEB_CONTENT": "web_content",
}

#: SpiderFoot module prefix we strip for readable ``source`` values.
_MODULE_PREFIX = "sfp_"


# ─── Small helpers ───────────────────────────────────────────────────


def _env_url() -> str:
    """Resolve the SpiderFoot base URL from the environment."""
    return os.environ.get("SPIDERFOOT_URL", DEFAULT_SPIDERFOOT_URL).rstrip("/")


def _env_token() -> str | None:
    """Resolve the optional bearer token from the environment."""
    token = os.environ.get("SPIDERFOOT_TOKEN", "").strip()
    return token or None


def _env_usecase() -> str:
    """Resolve the module-group use case (safe default: Passive)."""
    usecase = os.environ.get("SPIDERFOOT_USECASE", DEFAULT_USECASE).strip()
    if not usecase:
        return DEFAULT_USECASE
    # The web UI matches against capitalized group names ("Passive", ...).
    if usecase.lower() == "all":
        return "all"
    return usecase[0].upper() + usecase[1:]


def _category_from_type(event_type: str) -> str:
    """Map a SpiderFoot event type to our report category."""
    mapped = _EVENT_CATEGORY.get(event_type.upper())
    if mapped:
        return mapped
    # Fallback: DOMAIN_NAME_EXTRA -> "domain_name_extra"
    words = re.findall(r"[A-Z0-9]+(?:[a-z0-9]+)?", event_type)
    return "_".join(words).lower() if words else event_type.lower()


def _source_from_module(module: str) -> str:
    """Map a SpiderFoot module name (sfp_whois) to a readable source."""
    name = module or "spiderfoot"
    if name.startswith(_MODULE_PREFIX):
        name = name[len(_MODULE_PREFIX) :]
    return name or "spiderfoot"


def _confidence_from_values(confidence: Any, risk: Any, false_positive: Any) -> str:
    """Derive high/medium/low from SpiderFoot confidence %, risk, and fp flag."""
    if str(false_positive).strip() in ("1", "True", "true"):
        return "low"
    try:
        value = float(confidence)  # 0-100
    except (TypeError, ValueError):
        value = 50.0
    if value >= 70:
        return "high"
    if value >= 40:
        return "medium"
    return "low"


def _clean_text(value: Any) -> str:
    """Normalize a SpiderFoot data field to a safe, bounded string."""
    text = str(value or "").strip()
    text = html.unescape(text)  # scaneventresults HTML-escapes data/source
    text = re.sub(r"<SFURL>|</SFURL>", "", text)
    text = re.sub(r"\s+", " ", text)
    if len(text) > _MAX_FIELD_LENGTH:
        text = text[:_MAX_FIELD_LENGTH] + "…"
    return text


# ─── API calls ───────────────────────────────────────────────────────


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> Any:
    """Perform a JSON API call against the SpiderFoot web UI."""
    headers = {"Accept": "application/json"}
    token = _env_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.request(method, path, params=params, data=data, headers=headers)
    response.raise_for_status()
    return response.json()


def _start_scan(client: httpx.Client, target: str, scanname: str, usecase: str) -> str:
    """Start a scan and return its scan ID."""
    payload = {
        "scanname": scanname,
        "scantarget": target,
        "modulelist": "",
        "typelist": "",
        "usecase": usecase,
    }
    result = _request_json(client, "POST", "/startscan", data=payload)
    if not isinstance(result, list) or len(result) < 2 or result[0] != "SUCCESS":
        message = result[1] if isinstance(result, list) and len(result) > 1 else "unknown error"
        raise RuntimeError(f"SpiderFoot failed to start scan: {message}")
    scan_id = result[1]
    if not isinstance(scan_id, str) or not scan_id:
        raise RuntimeError("SpiderFoot returned an empty scan ID")
    return scan_id


def _scan_status(client: httpx.Client, scan_id: str) -> tuple[str, str]:
    """Return ``(status, target)`` for a scan; empty tuple on unknown scan."""
    result = _request_json(client, "GET", "/scanstatus", params={"id": scan_id})
    # [name, target, created, started, ended, status, riskmatrix]
    if not isinstance(result, list) or len(result) < 6:
        return "", ""
    status = result[5]
    target = result[1]
    return (str(status) if status else "", str(target) if target else "")


def _scan_results(client: httpx.Client, scan_id: str) -> list[list[Any]]:
    """Fetch all event rows for a scan (raw list-of-lists)."""
    result = _request_json(client, "GET", "/scaneventresults", params={"id": scan_id, "eventType": "ALL"})
    if not isinstance(result, list):
        return []
    return [row for row in result if isinstance(row, list)]


def _scan_errors(client: httpx.Client, scan_id: str) -> list[str]:
    """Fetch module-level error messages for a scan."""
    try:
        result = _request_json(client, "GET", "/scanerrors", params={"id": scan_id})
    except Exception as exc:
        logger.warning("spiderfoot: could not fetch scan errors: %s", exc)
        return []
    if not isinstance(result, list):
        return []
    messages: list[str] = []
    for item in result:
        text = str(item).strip()
        if text and text not in messages:
            messages.append(text)
    return messages[:100]


def _row_to_finding(row: list[Any]) -> OsintFinding | None:
    """Map one SpiderFoot ``scaneventresults`` row to an :class:`OsintFinding`."""
    # [lastseen, data, source_data, module, confidence, visibility, risk,
    #  hash, fp, parent_fp, event_type]
    if len(row) < 11:
        return None
    event_type = str(row[10] or "").upper()
    if event_type in _SKIP_EVENT_TYPES or event_type in _NOISE_EVENT_TYPES:
        return None

    module = str(row[3] or "")
    data_text = _clean_text(row[1])
    source_data = _clean_text(row[2])
    false_positive = str(row[8]).strip() in ("1", "True", "true")
    confidence = _confidence_from_values(row[4], row[6], row[8])

    summary = data_text
    if not summary:
        summary = f"{_source_from_module(module)} reported {event_type.lower()}"

    details: dict[str, Any] = {
        "event_type": event_type,
        "module": module,
        "false_positive": false_positive,
    }
    if source_data:
        details["source_data"] = source_data
    last_seen = _clean_text(row[0])
    if last_seen:
        details["last_seen"] = last_seen

    return OsintFinding(
        source=_source_from_module(module),
        category=_category_from_type(event_type),
        summary=summary,
        confidence=confidence,
        data=details,
    )


def _dedupe_findings(findings: list[OsintFinding]) -> list[OsintFinding]:
    """Remove exact duplicates (same source/category/summary) while keeping order."""
    seen: set[tuple[str, str, str]] = set()
    out: list[OsintFinding] = []
    for finding in findings:
        key = (finding.source, finding.category, finding.summary)
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


# ─── Entry point ─────────────────────────────────────────────────────


def lookup(target: str, target_type: str = "domain", *, timeout: int = 120) -> OsintReport:
    """Run a SpiderFoot scan against ``target`` and map results to a report.

    Args:
        target: The seed target (domain, email, username, wallet, IP, ...).
            SpiderFoot auto-detects the target type from the value.
        target_type: Our report label (domain | email | username | wallet |
            phone | ip). Used only for the report; the engine detects its own.
        timeout: Total budget in seconds for the *polling* phase, shared with
            the API requests. If a scan is still running when the budget runs
            out, the report is returned with whatever results arrived so far
            and a line in ``errors``.

    Returns:
        An :class:`OsintReport` with one finding per SpiderFoot event and one
        error string per failed module / poll timeout. Raises
        :class:`app.osint.OsintNotAvailable` only when SpiderFoot itself is
        unreachable or rejects the scan start (HTTP error, bad config).
    """
    if timeout < 1:
        raise ValueError("timeout must be >= 1")
    if not target or not target.strip():
        raise ValueError("target must not be empty")

    target = target.strip()
    report = OsintReport(target=target, target_type=target_type)
    base_url = _env_url()
    usecase = _env_usecase()

    try:
        with httpx.Client(base_url=base_url, timeout=max(10, timeout)) as client:
            try:
                scan_id = _start_scan(client, target, scanname=target[:64] or target, usecase=usecase)
            except (httpx.HTTPError, RuntimeError) as exc:
                if isinstance(exc, httpx.HTTPError):
                    raise OsintNotAvailable(f"SpiderFoot unreachable at {base_url}: {exc}") from exc
                # Scan rejected for a config reason — surface as unavailable-ish
                raise OsintNotAvailable(str(exc)) from exc

            deadline = time.monotonic() + timeout
            status = "STARTING"
            while status not in _TERMINAL_STATUSES:
                if time.monotonic() >= deadline:
                    report.errors.append(
                        f"spiderfoot: scan {scan_id} still running after {timeout}s (status {status}); "
                        "returning partial results"
                    )
                    break
                try:
                    status, _ = _scan_status(client, scan_id)
                except httpx.HTTPError as exc:
                    report.errors.append(f"spiderfoot: status poll failed: {exc}")
                    break
                if status not in _RUNNING_STATUSES and status not in _TERMINAL_STATUSES:
                    # Unknown state — treat as still going but log it.
                    logger.warning("spiderfoot: unknown scan status %r for scan %s", status, scan_id)
                    if time.monotonic() >= deadline:
                        break
                time.sleep(_POLL_INTERVAL_SECONDS)

            # Always attempt to collect whatever results exist, even on abort.
            try:
                rows = _scan_results(client, scan_id)
            except httpx.HTTPError as exc:
                report.errors.append(f"spiderfoot: could not fetch results: {exc}")
                rows = []

            findings: list[OsintFinding] = []
            for row in rows:
                finding = _row_to_finding(row)
                if finding is not None:
                    findings.append(finding)
            report.findings.extend(_dedupe_findings(findings))

            report.errors.extend(_scan_errors(client, scan_id))

            if status == "ERROR-FAILED":
                report.errors.append(f"spiderfoot: scan {scan_id} ended with ERROR-FAILED")
            elif status == "ABORTED":
                report.errors.append(f"spiderfoot: scan {scan_id} was aborted")
    except OsintNotAvailable:
        raise
    except httpx.HTTPError as exc:
        raise OsintNotAvailable(f"SpiderFoot unreachable at {base_url}: {exc}") from exc
    except Exception as exc:
        # Keep the contract: any engine-side failure is reported, not raised,
        # unless it was a connectivity problem (handled above).
        logger.exception("spiderfoot: unexpected lookup failure")
        report.errors.append(f"spiderfoot: unexpected error: {exc}")

    return report
