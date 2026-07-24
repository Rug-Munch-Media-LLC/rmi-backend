"""
admin_ops — fleet operations stubs.

Replace with real subprocess/docker/systemd calls when wired to infrastructure.
"""

from __future__ import annotations

import logging
import re
import shlex
import subprocess
import time
from typing import Any

logger = logging.getLogger("admin_bot.admin_ops")

# Commands that are NEVER allowed to execute remotely
# Each entry is a regex pattern (re.search) checked against the lowercased command.
_BLOCKED_COMMANDS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rm\s+-rf"),
    re.compile(r"\bdd\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r":\(\)\{"),
    re.compile(r"chmod\s+-r\s+"),  # any chmod -R … (recursive)
    re.compile(r"chmod\s+777"),  # chmod 777 … (world-writable)
    re.compile(r"(curl|wget)\s+.*\s*\|\s*(sh|bash)"),
)

# Allowed systemctl actions
_SYSTEMCTL_ACTIONS: frozenset[str] = frozenset(
    {"start", "stop", "restart", "status", "reload", "enable", "disable"}
)


def _validate_name(name: str) -> bool:
    """Validate container/service/docker-compose service name.

    Only alphanumeric, dash, underscore allowed.
    """
    return bool(name) and all(c.isalnum() or c in "-_" for c in name)


def _ok(output: str = "", duration: float = 0.0) -> dict[str, Any]:
    return {"ok": True, "output": output, "error": "", "duration": duration}


def _err(msg: str) -> dict[str, Any]:
    return {"ok": False, "output": "", "error": msg, "duration": 0.0}


def _run_subprocess(args: list[str], timeout: int = 30) -> dict[str, Any]:
    """Run list-args via subprocess (NO shell). Return unified result dict."""
    if not args:
        return _err("Empty command")
    t0 = time.time()
    try:
        r = subprocess.run(  # noqa: S603  — args validated by blocklist + allowlist
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": r.returncode == 0,
            "output": r.stdout or r.stderr,
            "error": r.stderr if r.returncode != 0 else "",
            "duration": time.time() - t0,
        }
    except subprocess.TimeoutExpired:
        return _err(f"Command timed out ({timeout}s)")
    except FileNotFoundError:
        return _err(f"Command not found: {args[0]}")
    except PermissionError:
        return _err(f"Permission denied: {args[0]}")
    except (KeyError, TypeError) as e :
        logger.debug("_run_subprocess_error", exc_info=True)
        return _err(str(e))


def run_shell(cmd: str, timeout: int = 30) -> dict[str, Any]:
    """Run a command safely via shlex.split() + shell=False.

    Checks the BLOCKLIST first. Strips trailing shell redirections
    (``2>&1``) which are unnecessary because ``capture_output=True``
    already captures stderr.
    """
    cmd_lower = cmd.lower()
    for pattern in _BLOCKED_COMMANDS:
        if pattern.search(cmd_lower):
            logger.warning("BLOCKED dangerous command: %s", cmd[:200])
            return _err(f"Command blocked: matches prohibited pattern /{pattern.pattern}/")

    # Strip trailing shell redirections that break shlex.split
    clean = cmd.strip()
    for suffix in (" 2>&1", "2>&1"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)].rstrip()

    try:
        parts = shlex.split(clean)
    except ValueError as exc:
        return _err(f"Invalid command syntax: {exc}")

    return _run_subprocess(parts, timeout=timeout)


def system_status() -> dict[str, Any]:
    return {"hostname": __import__("socket").gethostname(), "uptime": "stub"}


def format_status(s: dict[str, Any]) -> str:
    return f"🖥️ *Host:* `{s.get('hostname', '?')}`\nUptime: {s.get('uptime', '?')}"


def docker_ps() -> str:
    result = _run_subprocess(
        ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
        timeout=30,
    )
    return result.get("output", "")


def docker_stats() -> dict[str, Any]:
    return _run_subprocess(["docker", "stats", "--no-stream"], timeout=30)


def docker_restart(container: str) -> dict[str, Any]:
    if not _validate_name(container):
        return _err(f"Invalid container name: '{container}'")
    return _run_subprocess(["docker", "restart", container], timeout=60)


def docker_compose(action: str) -> dict[str, Any]:
    try:
        parts = shlex.split(action)
    except ValueError as exc:
        return _err(f"Invalid action syntax: {exc}")
    return _run_subprocess(["docker", "compose", *parts], timeout=120)


def docker_logs(service: str, tail: int = 50) -> dict[str, Any]:
    if not _validate_name(service):
        return _err(f"Invalid service name: '{service}'")
    return _run_subprocess(["docker", "logs", service, "--tail", str(tail)], timeout=30)


def service_list() -> str:
    result = _run_subprocess(
        ["systemctl", "list-units", "--type=service", "--no-pager"], timeout=30
    )
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


def trigger_backup(btype: str = "full") -> dict[str, Any]:
    return _ok(f"[stub] {btype} backup triggered")


def deploy_service(name: str) -> dict[str, Any]:
    if not _validate_name(name):
        return _err(f"Invalid service name: '{name}'")
    return _run_subprocess(["docker", "compose", "up", "-d", name], timeout=120)


def health_check() -> list[dict[str, Any]]:
    return [{"name": "stub", "status": "ok", "detail": "healthy"}]


def format_health(checks: list[dict[str, Any]]) -> str:
    lines = ["📊 *Health Check*\n"]
    for c in checks:
        emoji = {"ok": "✅", "fail": "❌", "warn": "⚠️"}.get(c.get("status", ""), "ℹ️")
        lines.append(f"{emoji} `{c['name']}` — {c.get('detail', '')}")
    return "\n".join(lines)
