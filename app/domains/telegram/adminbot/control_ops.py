"""Server-control primitives for the admin bot (fleet units, gates, data ops).

One place that knows how to *read* the fleet's state and how to *name* the
destructive actions. Every function here is read-only or returns a plan; the
actual mutation is executed by ``control_actions`` only after the operator has
confirmed it. That split is what makes the confirm gate auditable: a button
can never reach a mutating call without passing through the confirm step.

Units are addressed by a stable ``kind:name`` key so a single picker can list
docker containers and systemd services side by side without ambiguity.
"""

from __future__ import annotations

import re
from typing import Any

from app.domains.telegram.adminbot.admin_ops import _run_subprocess, _validate_name

# ── Fleet units the operator actually manages from Telegram ──────────────
# (kind, name, label). kind is "docker" or "systemd"; name is the exact
# container/unit name. Kept short and curated — a picker, not a dump.
FLEET_UNITS: tuple[tuple[str, str, str], ...] = (
    ("docker", "rmi-backend", "🖥 rmi-backend"),
    ("docker", "rmi-postgres", "🐘 postgres"),
    ("docker", "rmi-redis", "⚡ redis"),
    ("docker", "rmi-clickhouse", "📊 clickhouse"),
    ("docker", "rmi-qdrant", "🧠 qdrant"),
    ("docker", "rmi-neo4j", "🕸 neo4j"),
    ("systemd", "rmiadminbot", "🛡 adminbot"),
    ("systemd", "rugmunchbot", "🤖 rugmunchbot"),
    ("systemd", "rmi-backend", "🖥 backend (unit)"),
    ("systemd", "cc-reembed-watch", "🧬 reembed-watch"),
    ("systemd", "freshness-sweep", "🌿 freshness-sweep"),
)

# ── Gates: the four guards the operator watches ──────────────────────────
GATE_UNITS: tuple[tuple[str, str], ...] = (
    ("memory-constraint-gate", "🧠 memory-constraint"),
    ("cpu-saturation-gate", "📈 cpu-saturation"),
    ("qdrant-footprint-gate", "🧠 qdrant-footprint"),
    ("swap-pressure-gate", "💧 swap-pressure"),
)

# ── Data ops: systemd oneshot units the operator can trigger ─────────────
DATA_OPS: tuple[tuple[str, str, str], ...] = (
    ("sparse-rollout", "🧬 Sparse rollout", "cc-reembed-watch"),
    ("freshness", "🌿 Freshness sweep", "freshness-sweep"),
    ("retrieval-eval", "📊 Retrieval eval", "retrieval-eval-nightly"),
    ("rugbrain-eval", "🧠 RugBrain eval", "rugbrain-eval-nightly"),
    ("corpus-backup", "🗄 Corpus backup", "corpus-backup"),
    ("rmi-backup", "💾 RMI restic backup", "rmi-restic-backup"),
)

#: Actions that mutate state and therefore REQUIRE a confirm step.
DESTRUCTIVE_ACTIONS: frozenset[str] = frozenset({"stop", "restart", "start", "trigger"})

_UNIT_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


def valid_unit(name: str) -> bool:
    """A unit/container name is safe to pass to systemctl/docker as an argv."""
    return bool(name) and bool(_UNIT_RE.match(name)) and _validate_name(name.replace(".", "-"))


def unit_key(kind: str, name: str) -> str:
    """Stable ``kind:name`` key used in callback_data."""
    return f"{kind}:{name}"


def parse_unit_key(key: str) -> tuple[str, str] | None:
    """Split a ``kind:name`` key, validating both halves. None when malformed."""
    kind, _, name = key.partition(":")
    if kind not in ("docker", "systemd") or not valid_unit(name):
        return None
    return kind, name


def unit_status(kind: str, name: str) -> dict[str, Any]:
    """Read-only status of one unit (docker inspect / systemctl is-active)."""
    if not valid_unit(name):
        return {"ok": False, "output": "", "error": f"invalid unit '{name}'", "duration": 0.0}
    if kind == "docker":
        return _run_subprocess(
            ["docker", "inspect", "-f", "{{.State.Status}} | {{.State.StartedAt}}", name],
            timeout=15,
        )
    return _run_subprocess(["systemctl", "is-active", name], timeout=10)


def unit_logs(kind: str, name: str, tail: int = 40) -> dict[str, Any]:
    """Tail logs for a unit — docker logs or journalctl (read-only)."""
    if not valid_unit(name):
        return {"ok": False, "output": "", "error": f"invalid unit '{name}'", "duration": 0.0}
    tail = max(1, min(int(tail), 200))
    if kind == "docker":
        return _run_subprocess(["docker", "logs", name, "--tail", str(tail)], timeout=25)
    return _run_subprocess(
        ["journalctl", "-u", f"{name}.service", "--no-pager", "-n", str(tail), "-o", "short-iso"],
        timeout=25,
    )


def failed_units() -> dict[str, Any]:
    """List failed systemd units (read-only)."""
    return _run_subprocess(
        ["systemctl", "list-units", "--type=service", "--state=failed", "--no-pager", "--no-legend"],
        timeout=20,
    )


def gate_status() -> dict[str, Any]:
    """One screen: each gate's last result + next run (read-only)."""
    rows: list[str] = []
    for unit, label in GATE_UNITS:
        active = _run_subprocess(["systemctl", "is-active", f"{unit}.service"], timeout=8)
        result = _run_subprocess(
            ["systemctl", "show", f"{unit}.service", "-p", "ExecMainStatus", "-p", "Result"],
            timeout=8,
        )
        state = active.get("output", "?").strip() or "?"
        detail = " ".join(result.get("output", "").split())
        rows.append(f"{label}: {state} · {detail[:60]}")
    return {"ok": True, "output": "\n".join(rows), "error": "", "duration": 0.0}


def host_vitals() -> dict[str, Any]:
    """Load / memory / disk in one read-only call."""
    load = _run_subprocess(["cat", "/proc/loadavg"], timeout=5)
    mem = _run_subprocess(["free", "-h"], timeout=5)
    disk = _run_subprocess(["df", "-h", "/"], timeout=5)
    return {
        "ok": True,
        "output": f"LOAD {load.get('output', '?').strip()}\n{mem.get('output', '')}\n{disk.get('output', '')}",
        "error": "",
        "duration": 0.0,
    }
