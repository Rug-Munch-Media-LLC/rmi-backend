"""Render /diag results (pure — unit tested)."""

from __future__ import annotations

MAX_CHARS = 3800
MARKS = {"PASS": "✅", "FAIL": "❌", "TIMEOUT": "⏰", "SKIP": "⏭"}


def format_diag(rows: list[tuple[str, str, int, str]], elapsed: float) -> str:
    """Compact PASS/FAIL table for the operator."""
    bad = [row for row in rows if row[1] in ("FAIL", "TIMEOUT")]
    skipped = [row for row in rows if row[1] == "SKIP"]
    passed = len(rows) - len(bad) - len(skipped)
    head = f"🩺 COMMAND DIAG — {passed}/{len(rows)} pass · {elapsed:.1f}s"
    if skipped:
        head += f" · {len(skipped)} skipped (time budget)"
    lines = [head, ""]
    for name, status, ms, detail in rows:
        mark = MARKS.get(status, "❌")
        if status == "PASS":
            lines.append(f"{mark} /{name} — {ms}ms")
        elif detail:
            lines.append(f"{mark} /{name} — {detail}")
        else:
            lines.append(f"{mark} /{name}")
    if bad:
        lines.append("")
        lines.append("Failing: " + ", ".join(f"/{row[0]}" for row in bad))
    return "\n".join(lines)[:MAX_CHARS]
