"""
_ops_control — deep server-control reports: cgroup pressure, login audit,
cleanup plan, fleet digest.

Added 2026-08-23 (server-control expansion). Pure functions returning
dicts; command handlers live in ``_cmd_control``. Read-only by design —
``cleanup_plan`` reports reclaimable space but never mutates.
"""

from __future__ import annotations

from typing import Any

from app.domains.telegram.adminbot._ops_core import _run_subprocess

# Services whose cgroup pressure matters operationally (llama thrash
# incident 2026-08-22: memory.events was the signal netdata alerts would
# have missed while the TG token was invalid).
_WATCH_SERVICES = (
    "rmi-backend",
    "rugmunchbot",
    "rmiadminbot",
    "llama-qwen38",
)


def _read_file(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _psi_avg(text: str | None) -> str:
    """Extract avg10 from a PSI pressure file body ('-' when absent)."""
    if not text:
        return "-"
    for line in text.splitlines():
        if line.startswith("avg10="):
            return line.split(" ")[0].split("=")[1]
    return "-"


def _human_bytes(n: int) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit in ("B", "K") else f"{n:.1f}{unit}"
        n /= 1024  # type: ignore[assignment]
    return f"{n}T"


# ═══════════════════════════════════════════════
# CGROUP PRESSURE
# ═══════════════════════════════════════════════


def pressure_report(extra_services: list[str] | None = None) -> dict[str, Any]:
    """Per-service cgroup v2 memory/CPU pressure + OOM event counts."""
    services = list(_WATCH_SERVICES) + list(extra_services or [])
    rows: list[dict[str, Any]] = []
    for svc in services:
        if not all(c.isalnum() or c in "-_" for c in svc):
            continue
        base = f"/sys/fs/cgroup/system.slice/{svc}.service"
        cur = _read_file(f"{base}/memory.current")
        events = _read_file(f"{base}/memory.events")
        mem_psi = _read_file(f"{base}/memory.pressure")
        cpu_psi = _read_file(f"{base}/cpu.pressure")
        if cur is None and events is None:
            continue  # unit not running / no cgroup
        oom = killed = 0
        if events:
            for line in events.splitlines():
                parts = line.split()
                if len(parts) == 2:
                    if parts[0] == "oom":
                        oom = int(parts[1])
                    elif parts[0] == "killed":
                        killed = int(parts[1])

        rows.append(
            {
                "service": svc,
                "mem_current": int(cur) if cur else 0,
                "oom": oom,
                "killed": killed,
                "mem_avg10": _psi_avg(mem_psi),
                "cpu_avg10": _psi_avg(cpu_psi),
            }
        )
    psi_root = _read_file("/proc/pressure/memory")
    return {"rows": rows, "root_mem_psi": (psi_root or "").splitlines()[:1]}


def format_pressure(result: dict[str, Any]) -> str:
    lines = ["🩸 *Cgroup Pressure*\n"]
    for r in result.get("rows", []):
        mem = _human_bytes(int(r["mem_current"]))
        flags = []
        if r["oom"]:
            flags.append(f"OOM x{r['oom']}")
        if r["killed"]:
            flags.append(f"killed x{r['killed']}")
        flag_txt = f" ⚠️ {' '.join(flags)}" if flags else ""
        lines.append(f"`{r['service']:22s}` {mem:>9s} mem {r['mem_avg10']:>5s}% cpu {r['cpu_avg10']:>5s}%{flag_txt}")
    root = result.get("root_mem_psi") or []
    if root:
        lines.append(f"\n🌐 host memory PSI: `{root[0].strip()}`")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# LOGIN / SECURITY AUDIT
# ═══════════════════════════════════════════════


def login_audit() -> dict[str, Any]:
    who = _run_subprocess(["w", "-h"], timeout=5)
    last = _run_subprocess(["last", "-n", "8", "--timeformat", "%Y-%m-%d %H:%M"], timeout=5)
    f2b = _run_subprocess(["fail2ban-client", "status"], timeout=5)
    return {
        "sessions": who,
        "logins": last,
        "fail2ban": f2b,
    }


def format_logins(result: dict[str, Any]) -> str:
    lines = ["🔐 *Login Audit*\n"]
    sess = result.get("sessions", {})
    if sess.get("ok") and sess["output"].strip():
        lines.append("*Active sessions:*")
        lines.append("```")
        lines.extend(sess["output"].strip().splitlines()[:6])
        lines.append("```")
    else:
        lines.append("_No active user sessions._")
    logins = result.get("logins", {})
    if logins.get("ok") and logins["output"].strip():
        entries = [ln for ln in logins["output"].strip().splitlines() if ln.strip() and not ln.startswith("wtmp")][:8]
        lines.append("\n*Recent logins:*")
        lines.append("```")
        lines.extend(entries)
        lines.append("```")
    f2b = result.get("fail2ban", {})
    if f2b.get("ok") and f2b["output"].strip():
        # fail2ban-client status renders an ASCII tree; extract just the
        # jail list line ("`- Jail list:\tx y z") for a clean one-liner.
        jails = ""
        for ln in f2b["output"].splitlines():
            if "Jail list:" in ln:
                jails = ln.split("Jail list:")[1].strip()
                break
        if jails:
            lines.append(
                f"\n🛡 fail2ban jails ({jails.count(',') + 1 if ',' in jails else len(jails.split())}): `{jails[:200]}`"
            )
        else:
            lines.append("\n🛡 fail2ban: active")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# CLEANUP PLAN (dry-run only — never mutates)
# ═══════════════════════════════════════════════


def cleanup_plan() -> dict[str, Any]:
    journal = _run_subprocess(["journalctl", "--disk-usage"], timeout=10)
    apt = _run_subprocess(["sh", "-c", "du -sh /var/cache/apt 2>/dev/null || echo skip"], timeout=15)
    pip_cache = _run_subprocess(["sh", "-c", "du -sh /root/.cache/pip 2>/dev/null || echo skip"], timeout=30)
    docker_df = _run_subprocess(["docker", "system", "df"], timeout=30)
    return {
        "journal": journal,
        "apt": apt,
        "pip": pip_cache,
        "docker": docker_df,
    }


def format_cleanup(result: dict[str, Any]) -> str:
    lines = ["🧹 *Cleanup Plan* (dry-run — nothing deleted)\n"]
    j = result.get("journal", {})
    if j.get("ok"):
        lines.append(f"📜 journal: `{j['output'].strip()}`")
        lines.append("   → `journalctl --vacuum-size=500M`")
    apt = result.get("apt", {})
    if apt.get("ok") and "skip" not in apt["output"]:
        lines.append(f"📦 apt cache: `{apt['output'].strip()}` → `apt-get clean`")
    pip = result.get("pip", {})
    if pip.get("ok") and "skip" not in pip["output"]:
        lines.append(f"🐍 pip cache: `{pip['output'].strip()}` → `pip cache purge`")
    d = result.get("docker", {})
    if d.get("ok"):
        lines.append("\n🐳 *Docker:*")
        lines.append("```")
        lines.extend(d["output"].strip().splitlines()[:6])
        lines.append("```")
        lines.append("   → `/dprune` for safe docker reclamation")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# FLEET DIGEST
# ═══════════════════════════════════════════════


def fleet_digest() -> dict[str, Any]:
    failed = _run_subprocess(["systemctl", "list-units", "--state=failed", "--no-pager", "--no-legend"], timeout=15)
    disk = _run_subprocess(["df", "-h", "/"], timeout=5)
    mem = _run_subprocess(["free", "-m"], timeout=5)
    load = _run_subprocess(["cat", "/proc/loadavg"], timeout=3)
    docker_ps = _run_subprocess(["docker", "ps", "-q"], timeout=10)
    pressure = pressure_report()
    oom_total = sum(r["oom"] + r["killed"] for r in pressure.get("rows", []))
    return {
        "failed": failed,
        "disk": disk,
        "mem": mem,
        "load": load,
        "docker_count": len([x for x in docker_ps.get("output", "").splitlines() if x.strip()]),
        "oom_total": oom_total,
    }


def format_digest(result: dict[str, Any]) -> str:
    lines = ["📋 *Fleet Digest*\n"]
    failed = result.get("failed", {})
    if failed.get("ok"):
        # systemctl --no-legend lines start with a '●' bullet; strip it so
        # the unit NAME is shown, not the glyph (caught in live probe).
        units = [ln.replace("\u25cf", "").strip() for ln in failed["output"].strip().splitlines() if ln.strip()]
        units = [u for u in units if u]
        if units:
            lines.append(f"❌ *Failed units ({len(units)}):*")
            for u in units[:8]:
                lines.append(f"  `{u.split()[0]}`")
        else:
            lines.append("✅ No failed systemd units")
    disk = result.get("disk", {})
    if disk.get("ok"):
        parts = disk["output"].strip().splitlines()
        if len(parts) >= 2:
            p = parts[-1].split()
            lines.append(f"💾 Root: {p[2]} used of {p[1]} ({p[4]})")
    mem = result.get("mem", {})
    if mem.get("ok"):
        for ln in mem["output"].splitlines():
            if ln.startswith("Mem:"):
                p = ln.split()
                used, total = int(p[2]), int(p[1])
                pct = used * 100 // total if total else 0
                icon = "🔴" if pct > 90 else "🟡" if pct > 75 else "🟢"
                lines.append(f"{icon} RAM: {used}M / {total}M ({pct}%)")
    load = result.get("load", {})
    if load.get("ok"):
        lp = load["output"].split()
        lines.append(f"📊 Load: {lp[0]} / {lp[1]} / {lp[2]}")
    lines.append(f"🐳 Containers: {result.get('docker_count', '?')} running")
    oom = result.get("oom_total", 0)
    lines.append(f"{'⚠️' if oom else '✅'} Watched-service OOM/kill events: {oom}")
    return "\n".join(lines)
