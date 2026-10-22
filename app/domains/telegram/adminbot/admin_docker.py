"""
admin_docker — docker management operations.

Formatted ``docker ps`` table, tailed logs, stats snapshot sorted by
memory, and disk-usage / prune preview. Follows the admin_ops pattern:
list-arg subprocess (no shell) with strict container-name validation.
"""

from __future__ import annotations

import re
from typing import Any

from app.domains.telegram.adminbot.admin_ops import _run_subprocess, _validate_name, clip_text

_MEM_USAGE_RE = re.compile(r"^([\d.]+)([KMG]i?B)", re.IGNORECASE)
_HEALTH_RE = re.compile(r"\((healthy|unhealthy|health: starting)\)", re.IGNORECASE)
_UPTIME_RE = re.compile(r"^Up\s+(.+?)(?:\s+\((?:healthy|unhealthy|health: starting)\))?$", re.IGNORECASE)


# ═══════════════════════════════════════════════
# DOCKER PS (formatted table)
# ═══════════════════════════════════════════════


def _bytes_from_size(spec: str) -> float:
    """Convert a docker size token like '1.379GiB' / '393.6MiB' to bytes."""
    m = _MEM_USAGE_RE.match(spec.strip())
    if not m:
        return 0.0
    value = float(m.group(1))
    unit = m.group(2).lower()
    mult = {"b": 1, "kb": 1e3, "kib": 1024, "mb": 1e6, "mib": 1024**2, "gb": 1e9, "gib": 1024**3}[unit]
    return value * mult


def _health_from_status(status: str) -> str:
    """``healthy`` / ``unhealthy`` / ``health: starting`` from a docker status."""
    m = _HEALTH_RE.search(status or "")
    return m.group(1).lower() if m else ""


def _uptime_from_status(status: str) -> str:
    """The ``Up 7 minutes`` part of a docker status string."""
    m = _UPTIME_RE.match((status or "").strip())
    return f"Up {m.group(1).strip()}" if m else (status or "")[:24]


def docker_ps_table() -> dict[str, Any]:
    """docker ps as structured rows: name, state, health, uptime."""
    r = _run_subprocess(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"], timeout=30)
    if not r["ok"]:
        return r
    rows: list[dict[str, str]] = []
    for line in r.get("output", "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        rows.append(
            {
                "name": parts[0],
                "status": parts[1],
                "health": _health_from_status(parts[1]),
                "uptime": _uptime_from_status(parts[1]),
            }
        )
    return {**r, "rows": rows}


def _short_name(name: str) -> str:
    """Forgejo job containers carry 100+ char names - keep the readable tail."""
    return name if len(name) <= 40 else "…" + name[-39:]


def format_docker_ps(result: dict[str, Any]) -> str:
    """Render docker ps: unhealthy first, then health, then uptime per container."""
    rows: list[dict[str, str]] = result.get("rows", [])
    if not rows:
        if not result.get("ok", True):
            return f"❌ `docker ps` failed: {result.get('error', 'unknown')[:200]}"
        return "🐳 No running containers."
    unhealthy = [r for r in rows if r["health"] == "unhealthy"]
    starting = [r for r in rows if r["health"] == "health: starting"]
    rest = sorted((r for r in rows if r not in unhealthy and r not in starting), key=lambda r: (r["health"] != "healthy", r["name"].lower()))
    healthy_n = sum(1 for r in rows if r["health"] == "healthy")
    lines = [f"🐳 *Docker PS* — {len(rows)} running · {healthy_n} healthy · {len(rows) - healthy_n} unmonitored"]
    if unhealthy:
        lines.append(f"🔴 *UNHEALTHY ({len(unhealthy)})*: " + ", ".join(f"`{_short_name(r['name'])}`" for r in unhealthy))
    if starting:
        lines.append(f"🟡 health: starting ({len(starting)}): " + ", ".join(f"`{_short_name(r['name'])}`" for r in starting))
    lines.append("```")
    lines.append(f"{'CONTAINER':<42} {'HEALTH':<10} UPTIME")
    for r in unhealthy + starting + rest:
        health = r["health"] or "-"
        lines.append(f"{_short_name(r['name'])[:41]:<42} {health[:10]:<10} {r['uptime'][:22]}")
    lines.append("```")
    return clip_text("\n".join(lines))


# ═══════════════════════════════════════════════
# DOCKER LOGS (tail)
# ═══════════════════════════════════════════════


def docker_logs_tail(container: str, lines: int = 50) -> dict[str, Any]:
    """Tail docker logs for a container (default 50 lines)."""
    if not _validate_name(container):
        return {"ok": False, "output": "", "error": f"Invalid container name: '{container}'", "duration": 0.0}
    tail = max(1, min(int(lines), 500))
    return _run_subprocess(["docker", "logs", container, "--tail", str(tail), "--timestamps"], timeout=30)


# ═══════════════════════════════════════════════
# DOCKER RESTART (confirmation required by caller)
# ═══════════════════════════════════════════════


def docker_restart(container: str) -> dict[str, Any]:
    """Restart a container by exact name (allowlist-validated).

    Destructive: callers must present a confirmation step first.
    """
    if not _validate_name(container):
        return {"ok": False, "output": "", "error": f"Invalid container name: '{container}'", "duration": 0.0}
    return _run_subprocess(["docker", "restart", container], timeout=60)


# ═══════════════════════════════════════════════
# DOCKER STATS (top N by memory)
# ═══════════════════════════════════════════════


def docker_stats_top(limit: int = 10) -> dict[str, Any]:
    """docker stats snapshot, top N containers by memory usage."""
    r = _run_subprocess(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}"],
        timeout=30,
    )
    if not r["ok"]:
        return r
    rows: list[dict[str, Any]] = []
    for line in r.get("output", "").splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, mem_usage, mem_pct, cpu_pct = parts[0], parts[1], parts[2], parts[3]
        used_part = mem_usage.split("/", 1)[0].strip()
        rows.append(
            {
                "name": name,
                "mem_usage": mem_usage,
                "mem_bytes": _bytes_from_size(used_part),
                "mem_pct": mem_pct,
                "cpu_pct": cpu_pct,
            }
        )
    rows.sort(key=lambda x: x["mem_bytes"], reverse=True)
    return {**r, "rows": rows[:limit]}


def format_docker_stats(result: dict[str, Any]) -> str:
    """Render docker stats table."""
    rows: list[dict[str, Any]] = result.get("rows", [])
    if not rows:
        return f"❌ Docker stats failed: {result.get('error', 'no data')}"
    lines = ["📊 *Docker Stats* (top by memory)\n```"]
    lines.append(f"{'CONTAINER':<26} {'MEM USED':<16} {'MEM%':>6} {'CPU%':>6}")
    for row in rows:
        mem_short = row["mem_usage"][:15]
        lines.append(f"{row['name'][:25]:<26} {mem_short:<16} {row['mem_pct']:>6} {row['cpu_pct']:>6}")
    lines.append("```")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# DOCKER DISK USAGE / PRUNE
# ═══════════════════════════════════════════════


def docker_disk_usage() -> dict[str, Any]:
    """docker system df summary — reclaimable space by category."""
    r = _run_subprocess(["docker", "system", "df"], timeout=30)
    return r


def format_docker_df(result: dict[str, Any]) -> str:
    """Render docker system df output."""
    if not result.get("ok"):
        return f"❌ Docker disk usage failed: {result.get('error', '')}"
    lines = ["🧹 *Docker Disk Usage*\n```"]
    lines.append(result.get("output", "").strip())
    lines.append("```")
    lines.append("\n⚠️ Pruning removes unused images, containers, volumes and build cache.")
    return "\n".join(lines)


def docker_prune() -> dict[str, Any]:
    """Destructive: docker system prune -a --volumes (call after confirmation only)."""
    return _run_subprocess(["docker", "system", "prune", "-af", "--volumes"], timeout=300)
