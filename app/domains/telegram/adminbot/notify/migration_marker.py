"""Marker recording that migration completion was announced."""

from __future__ import annotations

import os

MARKER = "/root/.knowledge/.migration-notified"


def write_marker(value: str) -> None:
    """Record the announced vector count, so the alert is never sent twice."""
    os.makedirs(os.path.dirname(MARKER), exist_ok=True)
    with open(MARKER, "w", encoding="utf-8") as handle:
        handle.write(value)
