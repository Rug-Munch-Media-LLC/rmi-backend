"""
admin_server — server management operations.

Top processes, uptime, network, SSL cert expiry, and systemd service
reporting. Follows the admin_ops pattern: allowlist-safe, list-arg
subprocess (no shell), and formatted monospace output.
"""

from __future__ import annotations

import os
import re
from typing import Any

from app.domains.telegram.adminbot.admin_health import emoji_for_cert_days, parse_cert_enddate, parse_loadavg
from app.domains.telegram.adminbot.admin_ops import _run_subprocess, _validate_name, clip_text

_PS_HEADER = ("PID", "COMMAND", "%CPU", "%MEM", "RSS(MB)")
_SS_LINE_RE = re.compile(r"^\S+\s+\S+\s+\S+\s+(\S+)\s+\S+\s+(.*)$")
_SS_PROC_RE = re.compile(r"users:\(\(")


# ═══════════════════════════════════════════════
# TOP PROCESSES
# ═══════════════════════════════════════════════


def _parse_ps_output(output: str) -> list[dict[str, Any]]:
    """Parse ``ps -eo pid,comm,%cpu,%mem,rss`` rows (RSS in KB -> MB)."""
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        try:
            rows.append(
                {
                    "pid": int(parts[0]),
                    "command": parts[1][:24],
                    "cpu": float(parts[2]),
                    "mem": float(parts[3]),
                    "rss_mb": round(int(parts[4]) / 1024.0, 1),
                }
            )
        except ValueError:
            continue
    return rows


def top_processes(limit: int = 10, sort_by: str = "cpu") -> dict[str, Any]:
    """Top N processes by CPU (default) or memory. Pure parse of ps output."""
    sort_flag = "-%mem" if sort_by == "mem" else "-%cpu"
    r = _run_subprocess(
        ["ps", "-eo", "pid,comm,%cpu,%mem,rss", f"--sort={sort_flag}", "--no-headers"],
        timeout=10,
    )
    if not r["ok"]:
        return r
    rows = _parse_ps_output(r.get("output", ""))[:limit]
    return {**r, "rows": rows}


def format_top(result: dict[str, Any]) -> str:
    """Render top processes as an aligned monospace table."""
    rows: list[dict[str, Any]] = result.get("rows", [])
    if not rows:
        return f"❌ Top failed: {result.get('error', 'no data')}"
    lines = ["🔝 *Top Processes*\n", "```"]
    lines.append(f"{_PS_HEADER[0]:>7} {_PS_HEADER[1]:<24} {_PS_HEADER[2]:>5} {_PS_HEADER[3]:>5} {_PS_HEADER[4]:>8}")
    for row in rows:
        lines.append(
            f"{row['pid']:>7} {row['command']:<24} {row['cpu']:>5.1f} {row['mem']:>5.1f} {row['rss_mb']:>8.1f}"
        )
    lines.append("```")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# UPTIME
# ═══════════════════════════════════════════════


def uptime_report() -> dict[str, Any]:
    """System uptime + load averages + core count."""
    up = _run_subprocess(["uptime"], timeout=5)
    load_r = _run_subprocess(["cat", "/proc/loadavg"], timeout=3)
    nproc_r = _run_subprocess(["nproc"], timeout=3)
    try:
        nproc = int(nproc_r.get("output", "").strip() or "1")
    except ValueError:
        nproc = 1
    load = parse_loadavg(load_r.get("output", "")) if load_r.get("ok") else None
    return {
        "ok": up.get("ok", False),
        "output": up.get("output", ""),
        "error": up.get("error", ""),
        "load": load,
        "nproc": nproc,
    }


def format_uptime(result: dict[str, Any]) -> str:
    """Render uptime + load with status emoji."""

    lines = ["⏱ *Uptime*\n"]
    if result.get("output"):
        lines.append(f"```\n{result['output'].strip()}\n```")
    load = result.get("load")
    nproc = result.get("nproc", 1)
    if load:
        from app.domains.telegram.adminbot.admin_health import emoji_for_load

        lines.append(
            f"📈 Load: `{load[0]:.2f} / {load[1]:.2f} / {load[2]:.2f}` ({nproc} cores) {emoji_for_load(load[0], nproc)}"
        )
    if result.get("error"):
        lines.append(f"⚠️ {result['error']}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# NETWORK
# ═══════════════════════════════════════════════


def _parse_listening_ports(output: str) -> list[dict[str, str]]:
    """Parse ``ss -tlnp`` into {port, addr, proc} rows."""
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        m = _SS_LINE_RE.match(line)
        if not m:
            continue
        addr = m.group(1)
        rest = m.group(2)
        # Skip the ss header row ("Local Address:Port" gets split oddly).
        if addr in ("Local", "Address:Port") or "Address:Port" in addr:
            continue
        # Extract process from users:(("name",pid=..,fd=..))
        proc = ""
        pm = re.search(r'users:\(\(\s*"([^"]+)"', rest)
        if pm:
            proc = pm.group(1)
        # Port is after the last colon (handles [::1]:443)
        port = addr.rsplit(":", 1)[-1]
        rows.append({"addr": addr, "port": port, "proc": proc})
    return rows


def network_report(max_ports: int = 25, max_ifaces: int = 12) -> dict[str, Any]:
    """Network interfaces + listening ports summary."""
    ifaces = _run_subprocess(["ip", "-brief", "addr"], timeout=10)
    ss = _run_subprocess(["ss", "-tlnp"], timeout=10)
    ports = _parse_listening_ports(ss.get("output", "")) if ss.get("ok") else []
    # Dedupe by port (first listener wins) and cap output.
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for p in ports:
        if p["port"] not in seen:
            seen.add(p["port"])
            unique.append(p)
    # Filter ephemeral veth pairs (docker noise); keep meaningful interfaces.
    iface_lines: list[str] = []
    for line in ifaces.get("output", "").splitlines():
        name = line.split(None, 1)[0] if line.split() else ""
        if name.startswith("veth"):
            continue
        iface_lines.append(line)
    return {
        "ok": ifaces.get("ok", False) or ss.get("ok", False),
        "interfaces": "\n".join(iface_lines[:max_ifaces]),
        "iface_total": len(iface_lines),
        "ports": unique[:max_ports],
        "total_ports": len(unique),
        "error": ifaces.get("error") or ss.get("error", ""),
    }


def format_network(result: dict[str, Any]) -> str:
    """Render network summary."""
    lines = ["🌐 *Network*\n"]
    if result.get("interfaces"):
        lines.append(f"*Interfaces* ({result.get('iface_total', '?')} total):")
        for line in result["interfaces"].splitlines():
            parts = line.split(None, 2)
            if len(parts) >= 2:
                state = parts[1]
                emoji = "🟢" if state == "UP" else "⚪"
                addr = parts[2][:60] if len(parts) > 2 else "-"
                lines.append(f"  {emoji} `{parts[0]:<18}` {addr}")
    lines.append("")
    ports = result.get("ports", [])
    lines.append(f"*Listening TCP ports* ({result.get('total_ports', len(ports))} total, showing {len(ports)}):")
    lines.append("```")
    for p in ports:
        proc = f"({p['proc']})" if p.get("proc") else ""
        lines.append(f"  {p['port']:>6}  {p['addr'][:44]:<44} {proc}")
    lines.append("```")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# SSL CERTS
# ═══════════════════════════════════════════════


def _get_cert_enddate(path: str) -> Any | None:
    """Return expiry datetime for a PEM cert file, or None."""
    r = _run_subprocess(["openssl", "x509", "-enddate", "-noout", "-in", path], timeout=10)
    if not r.get("ok"):
        return None
    return parse_cert_enddate(r.get("output", ""))


def _scan_certbot() -> list[dict[str, Any]]:
    """Scan /etc/letsencrypt/live/* for cert expiry."""
    results: list[dict[str, Any]] = []
    live = "/etc/letsencrypt/live"
    if not os.path.isdir(live):
        return results
    try:
        domains = sorted(os.listdir(live))
    except OSError:
        return results
    for domain in domains:
        cert = os.path.join(live, domain, "fullchain.pem")
        if not os.path.isfile(cert):
            continue
        end = _get_cert_enddate(cert)
        results.append({"domain": domain, "source": "certbot", "expires": end})
    return results


def _scan_acmesh() -> list[dict[str, Any]]:
    """Scan /root/.acme.sh/<domain>/ for cert expiry."""
    results: list[dict[str, Any]] = []
    base = "/root/.acme.sh"
    if not os.path.isdir(base):
        return results
    skip = {"ca", "deploy", "dnsapi", "notify", "account.conf", "acme.sh"}
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return results
    for entry in entries:
        full = os.path.join(base, entry)
        if not os.path.isdir(full) or entry in skip:
            continue
        domain = entry.replace("_ecc", "")
        for cand in (os.path.join(full, f"{domain}.cer"), os.path.join(full, "fullchain.cer")):
            if os.path.isfile(cand):
                end = _get_cert_enddate(cand)
                if end:
                    results.append({"domain": domain, "source": "acme.sh", "expires": end})
                    break
    return results


def cert_expiry() -> dict[str, Any]:
    """All SSL certs (certbot + acme.sh) with expiry and days remaining."""
    import datetime as _dt

    now = _dt.datetime.now(_dt.UTC)
    certs = _scan_certbot() + _scan_acmesh()
    for c in certs:
        days = None
        if c.get("expires"):
            days = (c["expires"] - now).days
        c["days_left"] = days
        c["level"] = emoji_for_cert_days(days)
    certs.sort(key=lambda c: c["days_left"] if c["days_left"] is not None else 10**9)
    return {"ok": True, "certs": certs, "count": len(certs)}


def format_certs(result: dict[str, Any]) -> str:
    """Render SSL cert expiry table."""
    certs = result.get("certs", [])
    if not certs:
        return "🔒 No certificates found (checked certbot + acme.sh)."
    lines = ["🔒 *SSL Certificates*\n"]
    for c in certs:
        exp = c.get("expires")
        exp_txt = exp.strftime("%Y-%m-%d") if exp else "unknown"
        days = c.get("days_left")
        days_txt = f"{days}d" if days is not None else "?"
        lines.append(
            f"  {c.get('level', '🟡')} `{c['domain'][:32]:32s}` {exp_txt} ({days_txt}) [{c.get('source', '')}]"
        )
    lines.append(f"\n{result.get('count', len(certs))} certificates")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# SERVICES (systemd)
# ═══════════════════════════════════════════════


def services_report(filter_str: str = "", limit: int = 30) -> dict[str, Any]:
    """List systemd units, failed first, then active. Never hides a failure."""
    from app.domains.telegram.adminbot.admin_health import classify_systemd_status, parse_systemd_units

    r = _run_subprocess(["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"], timeout=30)
    if not r["ok"]:
        return r
    needle = (filter_str or "").lower()
    rows = [
        {**u, "level": classify_systemd_status(u.get("sub", ""), u.get("active", ""))}
        for u in parse_systemd_units(r.get("output", ""))
        if not needle or needle in u["unit"].lower()
    ]
    failed = sorted((u for u in rows if u["level"] == "🔴"), key=lambda u: u["unit"])
    active = [u for u in rows if u["level"] != "🔴"]
    shown = failed + active[: max(10, limit - len(failed))]
    return {**r, "rows": shown, "failed": len(failed), "active": len(active), "total": len(rows), "filter": needle}


def format_services(result: dict[str, Any]) -> str:
    """Render failed units first (full names), then the active ones that fit."""
    rows: list[dict[str, str]] = result.get("rows", [])
    if not rows:
        return "🛠 No services found" + (" matching filter." if result.get("total", 0) else ".")
    failed = [u for u in rows if u["level"] == "🔴"]
    rest = [u for u in rows if u["level"] != "🔴"]
    lines = [f"🛠 *Systemd Services* — {result.get('failed', len(failed))} failed / {result.get('total', len(rows))} loaded"]
    if failed:
        lines.append(f"\n🔴 *FAILED ({len(failed)})*")
        lines.extend(f"  `{u['unit'][:46]}` {u['sub'][:12]}" for u in failed)
    if rest:
        lines.append(f"\n🟢 *Active ({len(rest)} shown of {result.get('active', len(rest))})*\n```")
        lines.append(f"{'UNIT':<44} {'ACTIVE':<8} SUB")
        lines.extend(f"{u['level']} {u['unit'][:42]:<44} {u['active'][:8]:<8} {u['sub'][:10]}" for u in rest)
        lines.append("```")
    lines.append(f"\nShowing {len(rows)}/{result.get('total', len(rows))}")
    return clip_text("\n".join(lines))


# ═══════════════════════════════════════════════
# SERVICE RESTART (with validation)
# ═══════════════════════════════════════════════


def restart_service(name: str) -> dict[str, Any]:
    """Restart a systemd service by exact name (allowlist-validated)."""
    if not _validate_name(name):
        return {"ok": False, "output": "", "error": f"Invalid service name: '{name}'", "duration": 0.0}
    return _run_subprocess(["systemctl", "restart", name], timeout=60)


# ═══════════════════════════════════════════════
# SERVICE CONTROL (start / stop / restart)
# ═══════════════════════════════════════════════

_SERVICE_ACTIONS: frozenset[str] = frozenset({"start", "stop", "restart"})


def service_control(name: str, action: str) -> dict[str, Any]:
    """Start / stop / restart a systemd service (allowlist-validated).

    Destructive actions (stop/restart) must be confirmed by the caller.
    """
    if not _validate_name(name):
        return {"ok": False, "output": "", "error": f"Invalid service name: '{name}'", "duration": 0.0}
    if action not in _SERVICE_ACTIONS:
        return {"ok": False, "output": "", "error": f"Invalid action '{action}' (start|stop|restart)", "duration": 0.0}
    return _run_subprocess(["systemctl", action, name], timeout=60)


# ═══════════════════════════════════════════════
# SERVICE LOGS (journalctl tail)
# ═══════════════════════════════════════════════


def service_logs(name: str, tail: int = 50) -> dict[str, Any]:
    """Tail journalctl for a systemd service (default 50 lines)."""
    if not _validate_name(name):
        return {"ok": False, "output": "", "error": f"Invalid service name: '{name}'", "duration": 0.0}
    tail = max(1, min(int(tail), 500))
    return _run_subprocess(
        ["journalctl", "-u", f"{name}.service", "--no-pager", "-n", str(tail), "-o", "short-iso"],
        timeout=30,
    )


# ═══════════════════════════════════════════════
# REBOOT (scheduled, cancellable)
# ═══════════════════════════════════════════════


def schedule_reboot(delay_seconds: int = 60) -> dict[str, Any]:
    """Schedule a host reboot ``delay_seconds`` from now.

    Uses ``systemd-run --on-active`` so the reboot is owned by systemd and
    can be cancelled with ``systemctl cancel`` before it fires. Falls back
    to ``shutdown -r +N`` (also cancellable with ``shutdown -c``).
    """
    if delay_seconds < 10:
        delay_seconds = 10
    minutes = max(1, round(delay_seconds / 60.0))
    unit = f"rmi-admin-reboot-{int(__import__('time').time())}"
    r = _run_subprocess(
        ["systemd-run", "--on-active", f"{delay_seconds}", "--unit", unit, "systemctl", "reboot"],
        timeout=10,
    )
    if r["ok"]:
        return {
            "ok": True,
            "output": f"Reboot scheduled in {delay_seconds}s (unit {unit}). Cancel: /sh systemctl cancel {unit}",
            "error": "",
            "duration": r["duration"],
        }
    # Fallback: shutdown -r +N (minutes)
    r2 = _run_subprocess(["shutdown", "-r", f"+{minutes}", "RMI Admin Bot initiated reboot"], timeout=10)
    if r2["ok"]:
        return {
            "ok": True,
            "output": f"Reboot scheduled in ~{minutes} min (shutdown -r). Cancel: /sh shutdown -c",
            "error": "",
            "duration": r2["duration"],
        }
    return {
        "ok": False,
        "output": "",
        "error": r.get("error") or r2.get("error", "failed to schedule reboot"),
        "duration": 0.0,
    }
