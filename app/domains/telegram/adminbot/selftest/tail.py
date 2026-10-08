"""Render the resources and verdict tail of a self-test report."""

from __future__ import annotations

from typing import Any


def _resources_and_verdict(lines: list[str], results: dict[str, Any], issues: int) -> None:
    """Append the resources line and the closing verdict."""
    engine_ids = results.get("engines", [])
    lines.append("")
    lines.append(f"Engines available: {len(engine_ids)}/{results.get('engines_total', '?')} — " + ", ".join(engine_ids))

    res = results.get("resources", {})
    lines.append("")
    lines.append(
        f"Resources: disk {res.get('disk_used_pct')}% used ({res.get('disk_free_gb')}G free) · "
        f"mem {res.get('mem_avail_gb')}G avail"
    )

    lines.append("")
    if issues == 0:
        lines.append("Verdict: ✅ ALL SYSTEMS GO")
    else:
        lines.append(f"Verdict: ⚠️ {issues} issue(s) — see ❌ above")
