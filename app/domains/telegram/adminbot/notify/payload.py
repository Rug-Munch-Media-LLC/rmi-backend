"""Build Alertmanager-shaped alert payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def build(
    name: str,
    severity: str,
    summary: str,
    description: str = "",
    status: str = "firing",
) -> dict[str, Any]:
    """Return an alert the existing formatter renders correctly.

    The formatter reads labels.alertname and labels.severity, and puts
    annotations.summary first, so the headline goes in summary.
    """
    return {
        "status": status,
        "labels": {"alertname": name, "severity": severity},
        "annotations": {"summary": summary, "description": description},
        "startsAt": datetime.now(UTC).isoformat(timespec="seconds"),
    }
