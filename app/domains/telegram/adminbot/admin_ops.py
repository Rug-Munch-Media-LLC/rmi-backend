"""
admin_ops — fleet operations stubs.

Replace with real subprocess/docker/systemd calls when wired to infrastructure.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any


def _ok(output: str = "", duration: float = 0.0) -> dict[str, Any]:
    return {"ok": True, "output": output, "error": "", "duration": duration}


def _err(msg: str) -> dict[str, Any]:
    return {"ok": False, "output": "", "error": msg, "duration": 0.0}


def run_shell(cmd: str, timeout: int = 30) -> dict[str, Any]:
    t0 = time.time()
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return {
            "ok": r.returncode == 0,
            "output": r.stdout or r.stderr,
            "error": r.stderr if r.returncode != 0 else "",
            "duration": time.time() - t0,
        }
    except subprocess.TimeoutExpired:
        return _err(f"Command timed out ({timeout}s)")
    except Exception as e:
        return _err(str(e))


def system_status() -> dict[str, Any]:
    return {"hostname": __import__("socket").gethostname(), "uptime": "stub"}


def format_status(s: dict[str, Any]) -> str:
    return f"🖥️ *Host:* `{s.get('hostname', '?')}`\nUptime: {s.get('uptime', '?')}"


def docker_ps() -> str:
    return run_shell("docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}' 2>&1").get(
        "output", ""
    )


def docker_stats() -> dict[str, Any]:
    return run_shell("docker stats --no-stream 2>&1")


def docker_restart(container: str) -> dict[str, Any]:
    return run_shell(f"docker restart {container} 2>&1")


def docker_compose(action: str) -> dict[str, Any]:
    return run_shell(f"docker compose {action} 2>&1")


def docker_logs(service: str, tail: int = 50) -> dict[str, Any]:
    return run_shell(f"docker logs {service} --tail {tail} 2>&1")


def service_list() -> str:
    return run_shell("systemctl list-units --type=service --no-pager 2>&1 | head -30").get(
        "output", "no services"
    )


def service_control(name: str, action: str) -> dict[str, Any]:
    return run_shell(f"systemctl {action} {name} 2>&1")


def trigger_backup(btype: str = "full") -> dict[str, Any]:
    return _ok(f"[stub] {btype} backup triggered")


def deploy_service(name: str) -> dict[str, Any]:
    return run_shell(f"docker compose up -d {name} 2>&1")


def health_check() -> list[dict[str, Any]]:
    return [{"name": "stub", "status": "ok", "detail": "healthy"}]


def format_health(checks: list[dict[str, Any]]) -> str:
    lines = ["📊 *Health Check*\n"]
    for c in checks:
        emoji = {"ok": "✅", "fail": "❌", "warn": "⚠️"}.get(c.get("status", ""), "ℹ️")
        lines.append(f"{emoji} `{c['name']}` — {c.get('detail', '')}")
    return "\n".join(lines)
