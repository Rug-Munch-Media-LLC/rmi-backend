"""
_ops_system — system status, disk, memory, systemd, health reports.

Extracted from ``admin_ops`` (degodding 2026-08-14).
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.adminbot._ops_core import (
    _SYSTEMCTL_ACTIONS,
    _err,
    _run_subprocess,
    _validate_name,
)

# ═══════════════════════════════════════════════
# SYSTEM STATUS
# ═══════════════════════════════════════════════


def system_status() -> dict[str, Any]:
    result = _run_subprocess(["uptime", "-p"])
    uptime_out = result.get("output", "").strip() or "unknown"

    hostname = __import__("socket").gethostname()

    # Docker count
    docker_count = ""
    d_result = _run_subprocess(
        ["docker", "ps", "-q"],
        timeout=10,
    )
    if d_result["ok"]:
        count = len([line for line in d_result["output"].splitlines() if line.strip()])
        docker_count = f"\n🐳 Containers: {count} running"

    return {
        "hostname": hostname,
        "uptime": uptime_out + docker_count,
    }


def format_status(s: dict[str, Any]) -> str:
    return f"🖥️ *Host:* `{s.get('hostname', '?')}`\n⏱ Uptime: {s.get('uptime', '?')}"


# ═══════════════════════════════════════════════
# DISK
# ═══════════════════════════════════════════════


def disk_usage() -> dict[str, Any]:
    """Report disk usage for all mounted filesystems."""
    return _run_subprocess(
        ["df", "-h", "--type=ext4", "--type=xfs", "--type=btrfs", "--exclude-type=tmpfs", "--exclude-type=devtmpfs"],
        timeout=10,
    )


def format_disk(result: dict[str, Any]) -> str:
    if not result["ok"]:
        return f"❌ Disk check failed: {result['error']}"
    lines = ["💾 *Disk Usage*\n"]
    for line in result["output"].splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0] != "Filesystem":
            # Filesystem  Size  Used  Avail  Use%  Mounted
            lines.append(f"`{parts[5]:20s}` {parts[1]:>6s} used {parts[2]:>6s} of {parts[3]:>6s} ({parts[4]})")
        elif parts and parts[0] == "Filesystem":
            lines.append(f"`{'Mount':20s}` Size     Used     Avail    Use%")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# MEMORY / CPU
# ═══════════════════════════════════════════════


def memory_report() -> dict[str, Any]:
    """Report memory and CPU usage."""
    mem = _run_subprocess(["free", "-h"], timeout=5)
    cpu = _run_subprocess(["top", "-bn1", "|", "head", "-5"], timeout=5)
    # Actually use proper cpu stats
    cpu = _run_subprocess(
        ["sh", "-c", "top -bn1 | head -5"],
        timeout=5,
    )
    load = _run_subprocess(["cat", "/proc/loadavg"], timeout=3)
    return {"mem": mem, "cpu": cpu, "load": load}


def format_memory(result: dict[str, Any]) -> str:
    lines = ["🧠 *Memory & CPU*\n"]
    if result.get("mem", {}).get("ok"):
        lines.append("```")
        lines.append(result["mem"]["output"])
        lines.append("```")
    if result.get("cpu", {}).get("ok"):
        # Extract just the CPU line(s)
        for line in result["cpu"]["output"].splitlines():
            if "%Cpu" in line or "CPU" in line.upper():
                lines.append(line.strip())
    if result.get("load", {}).get("ok"):
        load_parts = result["load"]["output"].strip().split()
        if load_parts:
            lines.append(f"\n📊 *Load:* {load_parts[0]} / {load_parts[1]} / {load_parts[2]}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# SYSTEMD
# ═══════════════════════════════════════════════


def service_list() -> str:
    result = _run_subprocess(["systemctl", "list-units", "--type=service", "--no-pager"], timeout=30)
    if result["ok"] and result["output"]:
        lines = result["output"].splitlines()
        return "\n".join(lines[:30])
    return "no services"


def service_control(name: str, action: str) -> dict[str, Any]:
    if not _validate_name(name):
        return _err(f"Invalid service name: '{name}'")
    if action not in _SYSTEMCTL_ACTIONS:
        return _err(f"Invalid systemctl action: '{action}'")
    return _run_subprocess(["systemctl", action, name], timeout=30)


# ═══════════════════════════════════════════════
# HEALTH
# ═══════════════════════════════════════════════


def health_check() -> list[dict[str, Any]]:
    """Run health checks on core services."""
    checks = []
    services = {
        "RMI Backend": "http://localhost:8000/health",
        "Postgres": ("docker", "exec", "rmi-postgres", "pg_isready"),
        "Redis": ("docker", "exec", "rmi-redis", "redis-cli", "ping"),
    }
    for name, check in services.items():
        try:
            if isinstance(check, str) and check.startswith("http"):
                r = _run_subprocess(
                    ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "5", check],
                    timeout=8,
                )
                status = "ok" if r["output"].strip() in ("200", "302") else "fail"
                detail = f"HTTP {r['output'].strip()}" if status == "fail" else "healthy"
            else:
                r = _run_subprocess(list(check), timeout=5)
                status = "ok" if r["ok"] else "fail"
                detail = r["output"][:60] if status == "ok" else r["error"][:60]
        except (FileNotFoundError, PermissionError, OSError) as e:
            status = "fail"
            detail = str(e)[:60]
        checks.append({"name": name, "status": status, "detail": detail})
    return checks


def format_health(checks: list[dict[str, Any]]) -> str:
    lines = ["📊 *Health Check*\n"]
    for c in checks:
        emoji = {"ok": "✅", "fail": "❌", "warn": "⚠️"}.get(c.get("status", ""), "ℹ️")  # noqa: RUF001
        lines.append(f"{emoji} `{c['name']}` — {c.get('detail', '')}")
    return "\n".join(lines)
