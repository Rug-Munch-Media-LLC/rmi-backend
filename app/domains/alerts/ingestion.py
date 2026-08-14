"""Canonical platform-alert ingestion: normalize + dedupe.

Pure functions — no I/O, no network, no DB. Each detector's raw payload
shape is mapped into the canonical ``PlatformAlert`` shape by one branch
per source:

    whale | syndicate | mempool | launch | scanner | bulletin | security-intel

``normalize_alert`` returns ``None`` for malformed/unsupported payloads so
callers can drop them without exceptions. ``dedupe_alerts`` collapses
duplicates by the deterministic ``event_key`` (category + entity + source
+ 5-minute ts-bucket hash).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.domains.alerts.models import PlatformAlert, PlatformAlertSeverity

if TYPE_CHECKING:
    from collections.abc import Callable

VALID_SOURCES = frozenset(
    {
        "whale",
        "syndicate",
        "mempool",
        "launch",
        "scanner",
        "bulletin",
        "security-intel",
    }
)

# Whale watcher severity scale is 1-5; 1-2 = low, 3 = medium, 4 = high, 5 = critical.
_WHALE_INT_SEVERITY = {5: "critical", 4: "high", 3: "medium"}


def coerce_severity(value: Any, default: str = "medium") -> str:
    """Coerce a raw severity (str or int 1-5) to the canonical scale."""
    if isinstance(value, str) and value.strip().lower() in PlatformAlertSeverity.values():
        return value.strip().lower()
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return _WHALE_INT_SEVERITY.get(value, "low")
    return default


def _risk_to_severity(score: Any) -> str:
    """Map a 0-100 risk score to the canonical severity scale."""
    try:
        value = float(score)
    except (TypeError, ValueError):
        return "medium"
    if value >= 90:
        return "critical"
    if value >= 70:
        return "high"
    if value >= 50:
        return "medium"
    return "low"


def _first_text(*values: Any) -> str | None:
    """Return the first non-empty string, stripped."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _opt(value: Any) -> str | None:
    """Coerce a value to an optional trimmed string (None for empties)."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coerce_conf(value: Any, default: float = 0.5) -> float:
    """Coerce a confidence value into [0, 1]."""
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _as_urls(value: Any) -> list[str]:
    """Normalize a url/urls field into a list of strings."""
    if isinstance(value, list):
        return [u for u in value if isinstance(u, str) and u.strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _ts_of(raw: dict[str, Any]) -> str | None:
    """Pull an ISO timestamp out of a raw payload (best-effort)."""
    for key in ("ts", "timestamp", "created_at", "published_at", "fired_at", "time"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _intel_category(inner: dict[str, Any]) -> str:
    """Map security-intel labels to a canonical platform-alert category."""
    label = (f"{inner.get('type', '')} {inner.get('alert_type', '')} {inner.get('category', '')}").lower()
    if "honeypot" in label:
        return "honeypot"
    if "rug" in label:
        return "rug-risk"
    return "exploit"


# ── Source branches ────────────────────────────────────────────────────────


def _norm_whale(raw: dict[str, Any]) -> PlatformAlert | None:
    """Whale watcher: WhaleAlert-shaped dict or scan-alert dict."""
    title = _first_text(raw.get("message"), raw.get("title"), raw.get("alert_type"), raw.get("type"))
    if not title:
        return None
    return PlatformAlert(
        severity=coerce_severity(raw.get("severity"), default="high"),
        category="whale",
        title=title,
        body=_first_text(raw.get("message"), raw.get("description")) or "",
        token_address=_opt(raw.get("token_address") or raw.get("mint") or raw.get("wallet")),
        chain=_opt(raw.get("chain")) or "solana",
        source="whale",
        confidence=_coerce_conf(raw.get("confidence"), default=0.7),
        ts=_ts_of(raw) or _now(),
        urls=_as_urls(raw.get("urls")),
    )


def _norm_syndicate(raw: dict[str, Any]) -> PlatformAlert | None:
    """Syndicate tracker: _generate_alerts / SyndicateEvent-shaped dicts."""
    title = _first_text(raw.get("message"), raw.get("description"), raw.get("type"), raw.get("event_type"))
    if not title:
        return None
    return PlatformAlert(
        severity=coerce_severity(raw.get("severity") or raw.get("risk_flag"), default="high"),
        category="syndicate",
        title=title,
        body=_first_text(raw.get("description"), raw.get("message")) or "",
        token_address=_opt(raw.get("token_address") or raw.get("contract") or raw.get("mint")),
        chain=_opt(raw.get("chain")) or "solana",
        source="syndicate",
        confidence=_coerce_conf(raw.get("confidence"), default=0.8),
        ts=_ts_of(raw) or _now(),
        urls=_as_urls(raw.get("urls")),
    )


def _norm_mempool(raw: dict[str, Any]) -> PlatformAlert | None:
    """Mempool sentinel: attack detection dicts."""
    title = _first_text(raw.get("title"), raw.get("message"), raw.get("alert_type"), raw.get("type"))
    if not title:
        return None
    return PlatformAlert(
        severity=coerce_severity(raw.get("severity"), default="high"),
        category="mempool",
        title=title,
        body=_first_text(raw.get("description"), raw.get("message")) or "",
        token_address=_opt(raw.get("token_address") or raw.get("address") or raw.get("contract")),
        chain=_opt(raw.get("chain")) or "ethereum",
        source="mempool",
        confidence=_coerce_conf(raw.get("confidence"), default=0.6),
        ts=_ts_of(raw) or _now(),
        urls=_as_urls(raw.get("urls")),
    )


def _norm_launch(raw: dict[str, Any]) -> PlatformAlert | None:
    """Token launch detector: high-risk launch dicts."""
    token = _opt(raw.get("address") or raw.get("token_address") or raw.get("mint"))
    if not token:
        return None
    risk = raw.get("risk_score")
    flags = [str(f) for f in raw.get("risk_flags", []) if f] if isinstance(raw.get("risk_flags"), list) else []
    title = _first_text(raw.get("title"), raw.get("name"), raw.get("symbol")) or f"High-risk launch {token[:8]}..."
    body = _first_text(raw.get("message"), raw.get("description")) or (
        f"Risk score {risk}/100. Flags: {', '.join(flags)}." if risk is not None else ""
    )
    return PlatformAlert(
        severity=_risk_to_severity(risk),
        category="launch",
        title=title,
        body=body,
        token_address=token,
        chain=_opt(raw.get("chain")) or "solana",
        source="launch",
        confidence=_coerce_conf(raw.get("confidence"), default=0.6),
        ts=_ts_of(raw) or _now(),
        urls=_as_urls(raw.get("urls")),
    )


def _norm_scanner(raw: dict[str, Any]) -> PlatformAlert | None:
    """RMI scanner: news-feed article shape from post_scanner_alert."""
    title = _first_text(raw.get("title"))
    if not title:
        return None
    risk = raw.get("risk_score")
    severity = _risk_to_severity(risk) if risk is not None else "medium"
    urls = _as_urls(raw.get("url"))
    return PlatformAlert(
        severity=severity,
        category="scanner",
        title=title,
        body=_first_text(raw.get("description"), raw.get("body")) or "",
        token_address=_opt(raw.get("token_address") or raw.get("address")),
        chain=_opt(raw.get("chain")) or "unknown",
        source="scanner",
        confidence=_coerce_conf(raw.get("confidence"), default=0.6),
        ts=_ts_of(raw) or _now(),
        urls=urls,
    )


def _norm_bulletin(raw: dict[str, Any]) -> PlatformAlert | None:
    """Bulletin board: AlertIn-shaped dicts (title/description/severity/token)."""
    title = _first_text(raw.get("title"))
    if not title:
        return None
    return PlatformAlert(
        severity=coerce_severity(raw.get("severity"), default="medium"),
        category="bulletin",
        title=title,
        body=_first_text(raw.get("description"), raw.get("content")) or "",
        token_address=_opt(raw.get("token") or raw.get("token_address")),
        chain=_opt(raw.get("chain")),
        source="bulletin",
        confidence=_coerce_conf(raw.get("confidence"), default=0.5),
        ts=_ts_of(raw) or _now(),
        urls=_as_urls(raw.get("urls")),
    )


def _norm_security_intel(raw: dict[str, Any]) -> PlatformAlert | None:
    """Security dashboard: detection-wrapped or flat alert dicts."""
    detection = raw.get("detection")
    inner = detection if isinstance(detection, dict) else raw
    title = _first_text(
        inner.get("title"), inner.get("message"), inner.get("name"), inner.get("alert_type"), inner.get("type")
    )
    if not title:
        return None
    return PlatformAlert(
        severity=coerce_severity(inner.get("severity"), default="high"),
        category=_intel_category(inner),
        title=title,
        body=_first_text(inner.get("description"), inner.get("message")) or "",
        token_address=_opt(inner.get("token_address") or inner.get("address")),
        chain=_opt(inner.get("chain")) or "evm",
        source="security-intel",
        confidence=_coerce_conf(inner.get("confidence"), default=0.6),
        ts=_ts_of(raw) or _ts_of(inner) or _now(),
        urls=_as_urls(inner.get("urls")),
    )


_NORMALIZERS: dict[str, Callable[[dict[str, Any]], PlatformAlert | None]] = {
    "whale": _norm_whale,
    "syndicate": _norm_syndicate,
    "mempool": _norm_mempool,
    "launch": _norm_launch,
    "scanner": _norm_scanner,
    "bulletin": _norm_bulletin,
    "security-intel": _norm_security_intel,
}


def normalize_alert(source: str, raw: dict[str, Any]) -> PlatformAlert | None:
    """Map a raw detector payload to the canonical ``PlatformAlert``.

    Pure function. One branch per source; returns ``None`` on malformed or
    unsupported payloads (never raises for bad data).
    """
    if not isinstance(raw, dict) or not raw:
        return None
    source_key = (source or "").strip().lower()
    branch = _NORMALIZERS.get(source_key)
    if branch is None:
        return None
    try:
        return branch(raw)
    except (TypeError, ValueError, KeyError, AttributeError):
        return None


def dedupe_alerts(alerts: list[PlatformAlert]) -> list[PlatformAlert]:
    """Collapse duplicates by ``event_key``, preserving first-seen order."""
    seen: set[str] = set()
    deduped: list[PlatformAlert] = []
    for alert in alerts:
        key = alert.event_key or alert.derive_event_key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(alert)
    return deduped
