"""Render self-test results for Telegram."""

from __future__ import annotations

import socket
from typing import Any

from .tail import _resources_and_verdict


def format_selftest(results: dict[str, Any]) -> str:
    """Render the self-test card (pure — unit tested)."""
    host = socket.gethostname() or "this-host"
    lines: list[str] = [f"🩺 SELF-TEST — {host}", ""]
    issues = 0

    lines.append("Services")
    for unit, state in results.get("units", []):
        ok = state == "active"
        issues += 0 if ok else 1
        lines.append(f"{'✅' if ok else '❌'} {unit} — {state}")

    lines.append("")
    lines.append("Model lanes")
    for label, ok, ms in results.get("lanes", []):
        parked = "ff711" in label and not ok
        mark = "✅" if ok else ("💤" if parked else "❌")
        issues += 0 if (ok or parked) else 1
        suffix = f" — {ms}ms" if ok else (" — parked" if parked else " — down")
        lines.append(f"{mark} {label}{suffix}")

    lines.append("")
    lines.append("Alert pipeline")
    for queue, depth in results.get("queues", []):
        if depth < 0:
            issues += 1
            lines.append(f"❌ {queue} — redis unreachable")
        elif depth > 25:
            issues += 1
            lines.append(f"⚠️ {queue} — {depth} pending (consumer stalled?)")
        else:
            lines.append(f"✅ {queue} — {depth} pending")

    _resources_and_verdict(lines, results, issues)
    return "\n".join(lines)
