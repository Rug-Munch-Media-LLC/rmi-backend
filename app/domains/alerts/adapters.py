"""Public adapter layer: detector raw outputs -> canonical PlatformAlert pipeline.

Non-destructive wiring facade. Each ``adapt_*`` function maps ONE existing
detector's raw output dict into the canonical ``PlatformAlert`` shape by
delegating to the canonical normalizers in ``app.domains.alerts.ingestion``.
The per-source mapping rules (field extraction, severity coercion, category
assignment) live there, so adapters never duplicate or drift from the
pipeline. The source detector files are never imported or modified — only
their output dicts are consumed.

Pipeline: adapt -> dedupe (ingestion.dedupe_alerts) -> route
(publisher.route_alerts). Pure functions: no network, no DB, no I/O.

Category / severity mapping (canonical; see ingestion._NORMALIZERS)
--------------------------------------------------------------------
    source          category        severity (fallback when raw has no field)
    whale           whale           high          (int 1-5 -> low/medium/high/critical)
    syndicate       syndicate       high          (risk_flag honored)
    mempool         mempool         high
    launch          launch          medium        (risk_score 0-100 mapped)
    scanner         scanner         medium        (risk_score 0-100 mapped)
    bulletin        bulletin        medium
    security-intel  honeypot/rug-risk/exploit     high  (dashboard detection label)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.domains.alerts.ingestion import dedupe_alerts, normalize_alert
from app.domains.alerts.publisher import route_alerts

if TYPE_CHECKING:
    from app.domains.alerts.models import PlatformAlert

# Accepted adapter source keys -> canonical ingestion keys (ingestion.VALID_SOURCES).
_SOURCE_ALIASES: dict[str, str] = {
    "security_intel": "security-intel",
}


def _canonical_source(source: str) -> str:
    """Normalize a caller-supplied source key to the canonical ingestion key."""
    key = (source or "").strip().lower()
    return _SOURCE_ALIASES.get(key, key)


# ── Per-source adapters ────────────────────────────────────────────────────


def adapt_whale(raw: dict[str, Any]) -> PlatformAlert | None:
    """Whale watcher: ``WhaleAlert`` dataclass dict or scan-alert dict."""
    return adapt_alert("whale", raw)


def adapt_syndicate(raw: dict[str, Any]) -> PlatformAlert | None:
    """Syndicate tracker: ``_generate_alerts`` / ``SyndicateEvent``-shaped dict."""
    return adapt_alert("syndicate", raw)


def adapt_mempool(raw: dict[str, Any]) -> PlatformAlert | None:
    """Mempool sentinel: attack-detection dict (``get_alerts`` items)."""
    return adapt_alert("mempool", raw)


def adapt_launch(raw: dict[str, Any]) -> PlatformAlert | None:
    """Token launch detector: high-risk launch alert dict."""
    return adapt_alert("launch", raw)


def adapt_scanner(raw: dict[str, Any]) -> PlatformAlert | None:
    """RMI scanner: news-feed article shape from ``post_scanner_alert``."""
    return adapt_alert("scanner", raw)


def adapt_bulletin(raw: dict[str, Any]) -> PlatformAlert | None:
    """Bulletin board: ``AlertIn``-shaped dict (title/description/severity/token)."""
    return adapt_alert("bulletin", raw)


def adapt_security_intel(raw: dict[str, Any]) -> PlatformAlert | None:
    """Security dashboard: detection-wrapped or flat alert dict."""
    return adapt_alert("security-intel", raw)


# ── Dispatcher + batch ─────────────────────────────────────────────────────


def adapt_alert(source: str, raw: dict[str, Any]) -> PlatformAlert | None:
    """Map one raw detector payload for ``source`` to a canonical ``PlatformAlert``.

    Returns ``None`` for malformed, empty, or unsupported payloads (never
    raises for bad data).
    """
    return normalize_alert(_canonical_source(source), raw)


def adapt_batch(source: str, raw_items: list[dict[str, Any]]) -> list[PlatformAlert]:
    """Adapt every item in a detector batch, dropping malformed items."""
    adapted: list[PlatformAlert] = []
    for raw in raw_items:
        alert = adapt_alert(source, raw)
        if alert is not None:
            adapted.append(alert)
    return adapted


# ── Pipeline orchestrator ──────────────────────────────────────────────────


def collect_pipeline_alerts(source_outputs: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Adapt every detector batch, dedupe, and return the per-channel routing plan.

    ``source_outputs`` maps a source key to that detector's raw alert list
    (e.g. ``{"whale": [...], "scanner": [...]}``). Returns the routing plan
    produced by ``publisher.route_alerts`` — one entry per channel with the
    alerts that matched its severity threshold + category allowlist. The
    transport ``send`` callable is embedded in the plan but never invoked.
    """
    adapted: list[PlatformAlert] = []
    for source, raw_items in (source_outputs or {}).items():
        adapted.extend(adapt_batch(source, raw_items))
    deduped = dedupe_alerts(adapted)
    return route_alerts(deduped)
