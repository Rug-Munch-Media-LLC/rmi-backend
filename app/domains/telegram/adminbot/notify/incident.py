"""One incident's cross-worker state, ported from alert-webhook.py.

Two clocks, deliberately. ``last_seen`` is the absolute time of the last
sample, so it survives a worker restart. ``since`` is when the pending state
began: sliding ``last_seen`` must not also reset how long a breach has stood.
"""

from __future__ import annotations

from typing import Any

# last_seen: the incident is still live. last_sent: the operator was told.
# last_escalation: the level they were last told about. since: how long the
# current pending state has stood.
FIELDS = ("last_seen", "last_sent", "last_escalation", "since")


class Incident:
    """Fingerprint-keyed incident record."""

    def __init__(self, last_seen: float, last_sent: float, last_escalation: float, since: float) -> None:
        self.last_seen = last_seen
        self.last_sent = last_sent
        self.last_escalation = last_escalation
        self.since = since
        self.since_firing = True

    @staticmethod
    def decode(raw: Any) -> Incident | None:
        """Return a record from a stored mapping, or None when absent."""
        if not raw:
            return None
        record = Incident(*(_num(raw, name) for name in FIELDS))
        record.since_firing = str(raw.get("pending", "firing")) == "firing"
        return record

    def encode(self, pending: str) -> dict[str, str]:
        """Return the record as a flat Redis hash mapping."""
        self.since_firing = pending == "firing"
        return {name: f"{getattr(self, name):.3f}" for name in FIELDS} | {"pending": pending}


def _num(raw: Any, key: str) -> float:
    """Return one numeric field, treating anything unusable as 0.0."""
    try:
        return float(raw.get(key) or 0.0)
    except (TypeError, ValueError, AttributeError):
        return 0.0
