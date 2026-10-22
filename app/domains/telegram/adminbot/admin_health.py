"""
admin_health — quick status card and comprehensive health dashboard.

Pure data-gathering + formatting helpers for the /health command and the
Quick Status card shown at the top of /start. All parsing/classification
functions are pure (testable without a live bot); only the ``*_report``
functions shell out to the host.

Thresholds:
  disk  : >80% warn (🟡), >90% crit (🔴)
  memory: <5GB available warn, <2GB crit
  load  : >2x cores warn, >4x cores crit
  backup: >36h warn, >7d crit
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Any

from app.domains.telegram.adminbot.admin_ops import _run_subprocess

# ── Thresholds ──────────────────────────────────────────────────────
DISK_WARN_PCT = 80
DISK_CRIT_PCT = 90
MEM_WARN_GB = 5.0
MEM_CRIT_GB = 2.0
LOAD_WARN_MULT = 1.0  # load avg >= core count = fully loaded
LOAD_CRIT_MULT = 3.0
BACKUP_WARN_HOURS = 36.0
BACKUP_CRIT_HOURS = 7 * 24.0
CERT_WARN_DAYS = 30
CERT_CRIT_DAYS = 14

# Containers that must be up for the fleet to be considered healthy.
CRITICAL_SERVICES: tuple[str, ...] = (
    "rmi-backend",
    "rmi-clickhouse",
    "rmi-postgres",
    "rmi-redis",
)

# Human-readable labels for the control-panel health summary.
SERVICE_LABELS: dict[str, str] = {
    "rmi-backend": "Backend",
    "rmi-clickhouse": "ClickHouse",
    "rmi-postgres": "Postgres",
    "rmi-redis": "Redis",
}

# Systemd bot services tracked on the control panel.
BOT_SERVICES: tuple[str, ...] = ("rugmunchbot", "rmiadminbot")

# Full fleet dashboard service list (container name -> dashboard label).
# Order matches the /health dashboard output; bots are systemd-managed
# and tracked separately via BOT_SERVICES.
FLEET_CONTAINERS: tuple[tuple[str, str], ...] = (
    ("rmi-backend", "Backend"),
    ("rmi-postgres", "Postgres"),
    ("rmi-clickhouse", "ClickHouse"),
    ("rmi-redis", "Redis"),
    ("rmi-qdrant", "Qdrant"),
    ("degenfeed-meilisearch", "Meilisearch"),
)

# Backup sentinel files written by /root/scripts/backup-*.sh on success.
_BACKUP_SENTINEL = "/backups/.last-successful-backup"
_GIT_BACKUP_SENTINEL = "/backups/.last-successful-backup-all"

_PCT_RE = re.compile(r"(\d+)%")
_LOADAVG_RE = re.compile(r"([\d.]+)\s+([\d.]+)\s+([\d.]+)")
_MEM_LINE_RE = re.compile(r"Mem:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)")


# ═══════════════════════════════════════════════
# CLASSIFIERS (pure)
# ═══════════════════════════════════════════════


def emoji_for_pct(pct: float, warn: float = DISK_WARN_PCT, crit: float = DISK_CRIT_PCT) -> str:
    """Status emoji for a percentage threshold."""
    if pct >= crit:
        return "🔴"
    if pct >= warn:
        return "🟡"
    return "🟢"


def emoji_for_mem_available(gb: float) -> str:
    """Status emoji for available memory (GB)."""
    if gb < MEM_CRIT_GB:
        return "🔴"
    if gb < MEM_WARN_GB:
        return "🟡"
    return "🟢"


def emoji_for_load(load1: float, nproc: int) -> str:
    """Status emoji for 1-minute load average vs core count."""
    if nproc <= 0:
        return "🟡"
    ratio = load1 / nproc
    if ratio >= LOAD_CRIT_MULT:
        return "🔴"
    if ratio >= LOAD_WARN_MULT:
        return "🟡"
    return "🟢"


def emoji_for_hours_ago(hours: float | None) -> str:
    """Status emoji for age of last backup."""
    if hours is None:
        return "🔴"
    if hours >= BACKUP_CRIT_HOURS:
        return "🔴"
    if hours >= BACKUP_WARN_HOURS:
        return "🟡"
    return "🟢"


def emoji_for_cert_days(days: int | None) -> str:
    """Status emoji for days until certificate expiry."""
    if days is None:
        return "🔴"
    if days < CERT_CRIT_DAYS:
        return "🔴"
    if days < CERT_WARN_DAYS:
        return "🟡"
    return "🟢"


def classify_container_status(status: str) -> str:
    """Classify a docker container status string into 🟢/🟡/🔴."""
    low = (status or "").lower()
    if not low or "not found" in low:
        return "🔴"
    if any(k in low for k in ("exited", "unhealthy", "restarting", "dead", "paused")):
        return "🔴"
    if "health: starting" in low or "created" in low:
        return "🟡"
    if "up" in low:
        return "🟢"
    return "🟡"


def classify_systemd_status(sub: str, active: str = "") -> str:
    """Classify a systemd unit's SUB/ACTIVE state into 🟢/⚪/🔴/⚫."""
    low_sub = (sub or "").lower()
    low_active = (active or "").lower()
    if low_sub == "failed" or low_active == "failed":
        return "🔴"
    if low_sub == "running":
        return "🟢"
    if low_active == "inactive" or low_sub == "dead":
        return "⚫"
    if low_sub == "exited":
        return "⚪"
    return "🟡"


# ═══════════════════════════════════════════════
# PARSERS (pure — take raw command output)
# ═══════════════════════════════════════════════


def parse_df_pct(output: str) -> float | None:
    """Extract the highest Use% from ``df -h`` output."""
    best: float | None = None
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] != "Filesystem":
            m = _PCT_RE.search(parts[4])
            if m:
                pct = float(m.group(1))
                best = pct if best is None else max(best, pct)
    return best


def parse_free_memory(output: str) -> tuple[float, float] | None:
    """Parse ``free -m`` into (available_gb, total_gb)."""
    for line in output.splitlines():
        if line.startswith("Mem:"):
            m = _MEM_LINE_RE.search(line)
            if m:
                total_mb, _used, _free, _shared, _buff, avail_mb = (int(g) for g in m.groups())
                return avail_mb / 1024.0, total_mb / 1024.0
    return None


def parse_loadavg(output: str) -> tuple[float, float, float] | None:
    """Parse /proc/loadavg into (1m, 5m, 15m)."""
    m = _LOADAVG_RE.search(output)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2)), float(m.group(3))


def parse_cert_enddate(output: str) -> _dt.datetime | None:
    """Parse ``openssl x509 -enddate`` output (``notAfter=... GMT``)."""
    m = re.search(r"notAfter=(.+)", output or "")
    if not m:
        return None
    raw = m.group(1).strip()
    try:
        return _dt.datetime.strptime(raw, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=_dt.UTC)
    except ValueError:
        return None


def parse_systemd_units(output: str) -> list[dict[str, str]]:
    """Parse ``systemctl list-units --no-legend`` lines."""
    units: list[dict[str, str]] = []
    for line in output.splitlines():
        parts = [p for p in line.split(maxsplit=4) if p not in ("●", "*")]
        if len(parts) >= 4:
            units.append(
                {
                    "unit": parts[0],
                    "load": parts[1],
                    "active": parts[2],
                    "sub": parts[3],
                    "desc": parts[4] if len(parts) > 4 else "",
                }
            )
    return units


# ═══════════════════════════════════════════════
# GATHERERS (shell out)
# ═══════════════════════════════════════════════


def _host_uptime() -> str:
    r = _run_subprocess(["uptime", "-p"], timeout=5)
    return r.get("output", "").strip() or "unknown"


def _host_disk_pct() -> float | None:
    r = _run_subprocess(["df", "-h"], timeout=10)
    if not r.get("ok"):
        return None
    return parse_df_pct(r.get("output", ""))


def _host_memory_gb() -> tuple[float, float] | None:
    r = _run_subprocess(["free", "-m"], timeout=5)
    if not r.get("ok"):
        return None
    return parse_free_memory(r.get("output", ""))


def _host_load() -> tuple[float, float, float] | None:
    r = _run_subprocess(["cat", "/proc/loadavg"], timeout=3)
    if not r.get("ok"):
        return None
    return parse_loadavg(r.get("output", ""))


def _host_nproc() -> int:
    r = _run_subprocess(["nproc"], timeout=3)
    try:
        return int(r.get("output", "").strip() or "1")
    except ValueError:
        return 1


def _docker_counts() -> dict[str, int]:
    """Count running / total / exited docker containers."""
    running = len(
        [
            line
            for line in _run_subprocess(["docker", "ps", "-q"], timeout=10).get("output", "").splitlines()
            if line.strip()
        ]
    )
    total = len(
        [
            line
            for line in _run_subprocess(["docker", "ps", "-aq"], timeout=10).get("output", "").splitlines()
            if line.strip()
        ]
    )
    exited = 0
    r = _run_subprocess(["docker", "ps", "-a", "--filter", "status=exited", "-q"], timeout=10)
    if r.get("ok"):
        exited = len([line for line in r.get("output", "").splitlines() if line.strip()])
    return {"running": running, "total": total, "exited": exited}


def _systemd_failed() -> list[str]:
    r = _run_subprocess(["systemctl", "--failed", "--no-legend", "--no-pager"], timeout=15)
    failed: list[str] = []
    if not r.get("ok"):
        return failed
    for line in r.get("output", "").splitlines():
        parts = line.split()
        if parts and parts[0].endswith(".service"):
            failed.append(parts[0])
    return failed


def _read_sentinel_ts(path: str) -> _dt.datetime | None:
    """Read a unix-timestamp sentinel file (backup success markers)."""
    try:
        with open(path, encoding="utf-8") as f:
            ts = int(f.read().strip())
        return _dt.datetime.fromtimestamp(ts, tz=_dt.UTC)
    except (OSError, ValueError):
        return None


def _last_backup_info() -> dict[str, Any]:
    """Last successful backup (DB/app) + git backup ages in hours."""
    now = _dt.datetime.now(_dt.UTC)
    backup_ts = _read_sentinel_ts(_BACKUP_SENTINEL)
    git_ts = _read_sentinel_ts(_GIT_BACKUP_SENTINEL)
    backup_hours = (now - backup_ts).total_seconds() / 3600.0 if backup_ts else None
    git_hours = (now - git_ts).total_seconds() / 3600.0 if git_ts else None
    return {
        "last_backup": backup_ts,
        "backup_hours_ago": backup_hours,
        "last_git_backup": git_ts,
        "git_hours_ago": git_hours,
        "level": emoji_for_hours_ago(backup_hours),
    }


def container_status(name: str) -> dict[str, Any]:
    """Status of one container: running status string + level emoji."""
    r = _run_subprocess(["docker", "ps", "--filter", f"name=^{name}$", "--format", "{{.Status}}"], timeout=10)
    status = r.get("output", "").strip()
    if not status:
        r2 = _run_subprocess(
            ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Status}}"], timeout=10
        )
        status = r2.get("output", "").strip() or "not found"
    return {"name": name, "status": status, "level": classify_container_status(status)}


def critical_services_status() -> list[dict[str, Any]]:
    """Status for the fleet-critical containers."""
    return [container_status(name) for name in CRITICAL_SERVICES]


def fleet_services_status() -> list[dict[str, Any]]:
    """Status for every service shown on the /health dashboard.

    Returns one entry per fleet service (containers + the bots row),
    each with ``label``, ``name``, ``status`` and ``level`` emoji.
    """
    rows: list[dict[str, Any]] = []
    for name, label in FLEET_CONTAINERS:
        c = container_status(name)
        rows.append({"label": label, "name": name, "status": c["status"], "level": c["level"]})
    rows.append(_bots_row())
    return rows


def _bots_row() -> dict[str, Any]:
    """Combined status row for the systemd-managed bots (rugmunchbot + rmiadminbot)."""
    states = _bot_systemd_status()
    level = "🟢"
    if any(e == "🔴" for e in states.values()):
        level = "🔴"
    elif any(e == "🟡" for e in states.values()):
        level = "🟡"
    status = ", ".join(f"{svc}:{states.get(svc, '🟡')}" for svc in BOT_SERVICES)
    return {"label": "Bots", "name": "+".join(BOT_SERVICES), "status": status, "level": level}


def critical_alert_count(service_checks: list[dict[str, Any]] | None = None) -> int:
    """Count of critical issues: failed systemd units, exited containers, red services."""
    count = 0
    count += len(_systemd_failed())
    count += _docker_counts()["exited"]
    if service_checks is None:
        service_checks = critical_services_status()
    count += sum(1 for c in service_checks if c.get("level") == "🔴")
    return count


# ═══════════════════════════════════════════════
# QUICK STATUS CARD
# ═══════════════════════════════════════════════


def quick_status() -> dict[str, Any]:
    """Gather the quick status card data (uptime, disk%, mem%, load, alerts)."""
    nproc = _host_nproc()
    load = _host_load()
    mem = _host_memory_gb()
    disk_pct = _host_disk_pct()
    docker = _docker_counts()
    services = critical_services_status()

    load_level = emoji_for_load(load[0], nproc) if load else "🟡"
    mem_level = emoji_for_mem_available(mem[0]) if mem else "🟡"
    disk_level = emoji_for_pct(disk_pct) if disk_pct is not None else "🟡"

    return {
        "hostname": __import__("socket").gethostname(),
        "uptime": _host_uptime(),
        "disk_pct": disk_pct,
        "disk_level": disk_level,
        "mem_avail_gb": mem[0] if mem else None,
        "mem_total_gb": mem[1] if mem else None,
        "mem_level": mem_level,
        "load": load,
        "load_level": load_level,
        "nproc": nproc,
        "docker": docker,
        "systemd_failed": len(_systemd_failed()),
        "critical_alerts": critical_alert_count(services),
        "backup": _last_backup_info(),
    }


def format_quick_status(data: dict[str, Any]) -> str:
    """Render the quick status card with emoji indicators."""
    host = data.get("hostname", "host")
    lines = [f"📊 *Quick Status* — `{host}`\n"]
    lines.append(f"⏱ Uptime: `{data.get('uptime', '?')}`")

    disk = data.get("disk_pct")
    disk_txt = f"`{disk:.0f}%` used {data.get('disk_level', '🟡')}" if disk is not None else "`?` 🟡"
    lines.append(f"💾 Disk: {disk_txt}")

    mem = data.get("mem_avail_gb")
    if mem is not None:
        lines.append(f"🧠 Mem: `{mem:.1f}G` free of `{data.get('mem_total_gb', 0):.1f}G` {data.get('mem_level', '🟡')}")
    else:
        lines.append("🧠 Mem: `?` 🟡")

    load = data.get("load")
    if load:
        lines.append(
            f"📈 Load: `{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}` ({data.get('nproc', '?')} cores) "
            f"{data.get('load_level', '🟡')}"
        )
    else:
        lines.append("📈 Load: `?` 🟡")

    docker = data.get("docker", {})
    dock_emoji = "🟢" if docker.get("exited", 0) == 0 else "🟡"
    lines.append(f"🐳 Docker: `{docker.get('running', 0)}` running / `{docker.get('total', 0)}` total {dock_emoji}")

    failed = data.get("systemd_failed", 0)
    sys_emoji = "🟢" if failed == 0 else "🔴"
    lines.append(f"🛠 Systemd: `{failed}` failed units {sys_emoji}")

    backup = data.get("backup", {})
    b_hours = backup.get("backup_hours_ago")
    if b_hours is not None:
        lines.append(f"🗄 Backup: {b_hours:.1f}h ago {backup.get('level', '🟡')}")
    else:
        lines.append(f"🗄 Backup: no sentinel {backup.get('level', '🔴')}")

    alerts = data.get("critical_alerts", 0)
    alert_emoji = "🟢" if alerts == 0 else "🔴"
    lines.append(f"🚨 Critical alerts: `{alerts}` {alert_emoji}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# HEALTH DASHBOARD
# ═══════════════════════════════════════════════


def health_dashboard() -> dict[str, Any]:
    """Gather the comprehensive health dashboard data."""
    data = quick_status()
    # Re-gather pieces that need raw output for detail sections.
    df_r = _run_subprocess(["df", "-h"], timeout=10)
    data["df_output"] = df_r.get("output", "")
    mem_r = _run_subprocess(["free", "-h"], timeout=5)
    data["free_output"] = mem_r.get("output", "")
    failed = _systemd_failed()
    data["systemd_failed"] = failed
    data["services"] = critical_services_status()
    data["fleet_services"] = fleet_services_status()
    return data


def _format_df_lines(output: str) -> list[str]:
    """Compact df -h lines for the dashboard."""
    lines: list[str] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 6 and parts[0] != "Filesystem":
            m = _PCT_RE.search(parts[4])
            pct = float(m.group(1)) if m else 0.0
            lines.append(
                f"  {parts[5][:22]:22s} {parts[4]:>4s} {parts[1]:>6s} used {parts[2]:>6s}/{parts[3]:<6s} "
                f"{emoji_for_pct(pct)}"
            )
    return lines


def format_health_dashboard(data: dict[str, Any]) -> str:
    """Render the comprehensive health dashboard."""
    lines = ["📊 *System Health Dashboard*\n"]

    # ── Fleet services (backend, postgres, clickhouse, redis, bots, rag, meilisearch) ──
    fleet = data.get("fleet_services") or []
    if fleet:
        lines.append("🚨 *Services*")
        for s in fleet:
            label = s.get("label", s.get("name", "?"))
            status = (s.get("status") or "?")[:44]
            lines.append(f"  {s.get('level', '🟡')} `{label}` — {status}")
        lines.append("")

    # ── Disk ──
    disk = data.get("disk_pct")
    disk_emoji = data.get("disk_level", "🟡")
    disk_hdr = f"`{disk:.0f}%` peak" if disk is not None else "`?`"
    lines.append(f"💾 *Disk* ({disk_hdr} {disk_emoji})")
    df_lines = _format_df_lines(data.get("df_output", ""))
    if df_lines:
        lines.extend(df_lines[:8])
    else:
        lines.append("  no data")
    lines.append("")

    # ── Memory ──
    mem = data.get("mem_avail_gb")
    mem_emoji = data.get("mem_level", "🟡")
    mem_txt = f"`{mem:.1f}G` available" if mem is not None else "`?`"
    lines.append(f"🧠 *Memory* ({mem_txt} {mem_emoji})")
    free_out = data.get("free_output", "")
    if free_out:
        for line in free_out.splitlines():
            if line.startswith(("Mem:", "Swap:")):
                lines.append(f"  `{line.strip()}`")
    lines.append("")

    # ── CPU load ──
    load = data.get("load")
    load_emoji = data.get("load_level", "🟡")
    if load:
        lines.append(
            f"📈 *CPU Load* ({data.get('nproc', '?')} cores): "
            f"`{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}` {load_emoji}"
        )
    else:
        lines.append(f"📈 *CPU Load*: `?` {load_emoji}")
    lines.append("")

    # ── Docker ──
    docker = data.get("docker", {})
    dock_emoji = "🟢" if docker.get("exited", 0) == 0 else "🟡"
    lines.append(
        f"🐳 *Docker*: `{docker.get('running', 0)}` running / `{docker.get('total', 0)}` total "
        f"(`{docker.get('exited', 0)}` exited) {dock_emoji}"
    )

    # ── Systemd ──
    failed = data.get("systemd_failed", [])
    if isinstance(failed, list):
        sys_emoji = "🟢" if not failed else "🔴"
        lines.append(f"🛠 *Systemd*: `{len(failed)}` failed units {sys_emoji}")
        if failed:
            for u in failed[:5]:
                lines.append(f"  🔴 `{u}`")
    else:
        lines.append(f"🛠 *Systemd*: `{failed}` failed units {'🟢' if failed == 0 else '🔴'}")
    lines.append("")

    # ── Backup ──
    backup = data.get("backup", {})
    b_hours = backup.get("backup_hours_ago")
    b_emoji = backup.get("level", "🔴")
    b_ts = backup.get("last_backup")
    if b_hours is not None:
        b_ts_txt = b_ts.strftime("%Y-%m-%d %H:%M UTC") if b_ts else "unknown"
        lines.append(f"🗄 *Backup*: last success `{b_ts_txt}` ({b_hours:.1f}h ago) {b_emoji}")
    else:
        lines.append(f"🗄 *Backup*: no sentinel {b_emoji}")
    git_hours = backup.get("git_hours_ago")
    if git_hours is not None:
        lines.append(f"   Git push backup: {git_hours:.1f}h ago")
    lines.append("")

    # ── Critical services (legacy view) ──
    if not fleet:
        lines.append("🚨 *Critical Services*")
        for c in data.get("services", []):
            lines.append(f"  {c.get('level', '🟡')} `{c.get('name', '?')}` — {c.get('status', '?')[:48]}")
        lines.append("")

    alerts = data.get("critical_alerts", 0)
    lines.append(f"🚨 *Total critical alerts*: `{alerts}` {'🟢' if alerts == 0 else '🔴'}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════
# CONTROL PANEL (v5 — /start dashboard)
# ═══════════════════════════════════════════════


def _bot_systemd_status() -> dict[str, str]:
    """Systemd active state for the fleet bots (rugmunchbot / rmiadminbot)."""
    result: dict[str, str] = {}
    for svc in BOT_SERVICES:
        r = _run_subprocess(["systemctl", "is-active", f"{svc}.service"], timeout=5)
        state = r.get("output", "").strip() or "unknown"
        if state == "active":
            result[svc] = "🟢"
        elif state in ("inactive", "dead", "failed"):
            result[svc] = "🔴"
        else:
            result[svc] = "🟡"
    return result


def _service_summary() -> dict[str, Any]:
    """Label -> status emoji for the fleet containers + bots."""
    summary: dict[str, Any] = {}
    for name, label in FLEET_CONTAINERS:
        c = container_status(name)
        summary[label] = c.get("level", "🟡")
    summary["Bots"] = _bot_systemd_status()
    return summary


def control_panel() -> dict[str, Any]:
    """Gather the /start control panel data (health + quick stats + bots)."""
    data = quick_status()
    data["service_summary"] = _service_summary()
    return data


def format_control_panel(data: dict[str, Any]) -> str:
    """Render the /start control panel dashboard."""
    host = data.get("hostname", "host")
    lines = [f"🛡️ *RMI Admin Control Panel* — `{host}`\n"]

    # ── System health summary ──
    summary = data.get("service_summary", {})
    if summary:
        lines.append("🚨 *Service Health*")
        row: list[str] = []
        for label, emoji in summary.items():
            if isinstance(emoji, dict):  # bots map
                row.append(f"{emoji.get('rugmunchbot', '🟡')} Bot")
                row.append(f"{emoji.get('rmiadminbot', '🟡')} AdminBot")
            else:
                row.append(f"{emoji} {label}")
        lines.append(" ".join(row))
        lines.append("")

    # ── Quick stats ──
    lines.append("📊 *Quick Stats*")
    disk = data.get("disk_pct")
    disk_txt = f"`{disk:.0f}%` {data.get('disk_level', '🟡')}" if disk is not None else "`?` 🟡"
    lines.append(f"💾 Disk: {disk_txt}")

    mem = data.get("mem_avail_gb")
    if mem is not None:
        lines.append(f"🧠 Mem: `{mem:.1f}G` free of `{data.get('mem_total_gb', 0):.1f}G` {data.get('mem_level', '🟡')}")
    else:
        lines.append("🧠 Mem: `?` 🟡")

    load = data.get("load")
    if load:
        lines.append(
            f"📈 Load: `{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}` ({data.get('nproc', '?')} cores) "
            f"{data.get('load_level', '🟡')}"
        )
    else:
        lines.append("📈 Load: `?` 🟡")

    docker = data.get("docker", {})
    dock_emoji = "🟢" if docker.get("exited", 0) == 0 else "🟡"
    lines.append(f"🐳 Docker: `{docker.get('running', 0)}` running / `{docker.get('total', 0)}` total {dock_emoji}")

    failed = data.get("systemd_failed", 0)
    sys_emoji = "🟢" if failed == 0 else "🔴"
    lines.append(f"🛠 Systemd: `{failed}` failed units {sys_emoji}")

    backup = data.get("backup", {})
    b_hours = backup.get("backup_hours_ago")
    if b_hours is not None:
        lines.append(f"🗄 Backup: {b_hours:.1f}h ago {backup.get('level', '🟡')}")
    else:
        lines.append(f"🗄 Backup: no sentinel {backup.get('level', '🔴')}")

    alerts = data.get("critical_alerts", 0)
    lines.append(f"🚨 Alerts: `{alerts}` {'🟢' if alerts == 0 else '🔴'}")
    lines.append("")

    lines.append("_Owner-only · all commands logged · pick a section below:_")
    return "\n".join(lines)
