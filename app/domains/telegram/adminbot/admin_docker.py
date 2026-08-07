"""
admin_docker — docker management operations.

Formatted ``docker ps`` table, tailed logs, stats snapshot sorted by
memory, and disk-usage / prune preview. Follows the admin_ops pattern:
list-arg subprocess (no shell) with strict container-name validation.
"""

from __future__ import annotations

import re
from typing import Any

from app.domains.telegram.adminbot.admin_ops import _run_subprocess, _validate_name

_MEM_USAGE_RE = re.compile(r"^([\d.]+)([KMG]i?B)", re.IGNORECASE)
_STATS_HEADER = ("CONTAINER", "MEM USED", "MEM%", "CPU%")


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


def docker_ps_table() -> dict[str, Any]:
    """docker ps formatted as name / status / ports table."""
    r = _run_subprocess(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"],
        timeout=30,
    )
    if not r["ok"]:
        return r
    rows: list[tuple[str, str, str]] = []
    for line in r.get("output", "").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
    return {**r, "rows": rows}


def format_docker_ps(result: dict[str, Any]) -> str:
    """Render docker ps as an aligned table (name, status, uptime, ports)."""
    rows: list[tuple[str, str, str]] = result.get("rows", [])
    if not rows:
        return "🐳 No running containers."
    lines = ["🐳 *Docker PS*\n```"]
    lines.append(f"{'CONTAINER':<26} {'STATUS':<32} PORTS")
    for name, status, ports in rows:
        ports_short = (ports[:34] + "…") if len(ports) > 35 else ports
        status_short = status[:30]
        lines.append(f"{name[:25]:<26} {status_short:<32} {ports_short}")
    lines.append("```")
    lines.append(f"\n{len(rows)} running")
    return "\n".join(lines)


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
