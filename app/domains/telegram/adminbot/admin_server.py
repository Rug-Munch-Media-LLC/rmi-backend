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
from app.domains.telegram.adminbot.admin_ops import _run_subprocess, _validate_name

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
    """List systemd services, optionally filtered, with status classification."""
    from app.domains.telegram.adminbot.admin_health import classify_systemd_status, parse_systemd_units

    r = _run_subprocess(["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"], timeout=30)
    if not r["ok"]:
        return r
    units = parse_systemd_units(r.get("output", ""))
    needle = (filter_str or "").lower()
    rows: list[dict[str, str]] = []
    for u in units:
        if needle and needle not in u["unit"].lower():
            continue
        u["level"] = classify_systemd_status(u.get("sub", ""), u.get("active", ""))
        rows.append(u)
    # Failed first, then running, keep stable order otherwise.
    order = {"🔴": 0, "🟡": 1, "🟢": 2, "⚪": 3, "⚫": 4}
    rows.sort(key=lambda u: order.get(u["level"], 5))
    return {**r, "rows": rows[:limit], "total": len(rows)}


def format_services(result: dict[str, Any]) -> str:
    """Render systemd services with status colors."""
    rows: list[dict[str, str]] = result.get("rows", [])
    if not rows:
        needle_note = " matching filter" if result.get("total", 0) else ""
        return f"🛠 No services found{needle_note}."
    lines = ["🛠 *Systemd Services*\n"]
    lines.append("```")
    lines.append(f"{'UNIT':<44} {'ACTIVE':<8} {'SUB':<10}")
    for u in rows:
        lines.append(f"{u['level']} {u['unit'][:42]:<44} {u['active'][:8]:<8} {u['sub'][:10]:<10}")
    lines.append("```")
    total = result.get("total", len(rows))
    shown = len(rows)
    lines.append(f"\nShowing {shown}/{total} services")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# SERVICE RESTART (with validation)
# ═══════════════════════════════════════════════


def restart_service(name: str) -> dict[str, Any]:
    """Restart a systemd service by exact name (allowlist-validated)."""
    if not _validate_name(name):
        return {"ok": False, "output": "", "error": f"Invalid service name: '{name}'", "duration": 0.0}
    return _run_subprocess(["systemctl", "restart", name], timeout=60)
