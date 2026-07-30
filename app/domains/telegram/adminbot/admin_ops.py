"""
admin_ops — fleet operations.

Shell execution, Docker, systemd, backup, deploy, disk, memory,
git, push, chain status, and alert dispatch.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import subprocess
import time
from typing import Any

logger = logging.getLogger("admin_bot.admin_ops")

# Commands that are NEVER allowed to execute remotely
_BLOCKED_COMMANDS: tuple[re.Pattern[str], ...] = (
    re.compile(r"rm\s+-rf"),
    re.compile(r"\bdd\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r":\(\)\{"),
    re.compile(r"chmod\s+-r\s+"),
    re.compile(r"chmod\s+777"),
    re.compile(r"(curl|wget)\s+.*\s*\|\s*(sh|bash)"),
)

# Allowed systemctl actions
_SYSTEMCTL_ACTIONS: frozenset[str] = frozenset(
    {"start", "stop", "restart", "status", "reload", "enable", "disable"}
)

# Known RugMunch repos for git/push operations
RMI_REPOS: list[str] = [
    "rmi-backend",
    "rmi-frontend",
    "fleet-infra",
    "pryscraper",
    "walletpress",
    "rugmunchbot",
    "standards",
    "rmi-rag",
    "rmi-mcp-x402",
    "rmi-docs",
]


def _validate_name(name: str) -> bool:
    """Validate container/service/repo name — alphanumeric, dash, underscore only."""
    return bool(name) and all(c.isalnum() or c in "-_" for c in name)


def _ok(output: str = "", duration: float = 0.0) -> dict[str, Any]:
    return {"ok": True, "output": output, "error": "", "duration": duration}


def _err(msg: str) -> dict[str, Any]:
    return {"ok": False, "output": "", "error": msg, "duration": 0.0}


def _run_subprocess(args: list[str], timeout: int = 30, cwd: str | None = None) -> dict[str, Any]:
    """Run list-args via subprocess (NO shell). Return unified result dict."""
    if not args:
        return _err("Empty command")
    t0 = time.time()
    try:
        r = subprocess.run(
            args,
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
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
    except (KeyError, TypeError, OSError, ValueError) as e:
        logger.debug("_run_subprocess_error", exc_info=True)
        return _err(str(e))


# ═══════════════════════════════════════════════
# SHELL
# ═══════════════════════════════════════════════


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

    clean = cmd.strip()
    for suffix in (" 2>&1", "2>&1"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)].rstrip()

    try:
        parts = shlex.split(clean)
    except ValueError as exc:
        return _err(f"Invalid command syntax: {exc}")

    return _run_subprocess(parts, timeout=timeout)


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
        count = len([l for l in d_result["output"].splitlines() if l.strip()])
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
    return _run_subprocess(["df", "-h", "--type=ext4", "--type=xfs", "--type=btrfs", "--exclude-type=tmpfs", "--exclude-type=devtmpfs"], timeout=10)


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
# DOCKER
# ═══════════════════════════════════════════════


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
    return _run_subprocess(
        ["docker", "logs", service, "--tail", str(tail), "--timestamps"],
        timeout=30,
    )


# ═══════════════════════════════════════════════
# SYSTEMD
# ═══════════════════════════════════════════════


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


# ═══════════════════════════════════════════════
# BACKUP
# ═══════════════════════════════════════════════


def trigger_backup(btype: str = "full") -> dict[str, Any]:
    """Trigger a backup script."""
    scripts = {
        "full": "/root/scripts/push-all-repos.sh",
        "db": "/root/scripts/db-backup-all.sh",
        "status": "",
    }
    script = scripts.get(btype)
    if btype == "status":
        return _ok("📊 *Backup Status*\n\nSee: `/root/scripts/` for backup scripts\nLast backup: check backup logs")
    if not script or not os.path.exists(script):
        return _err(f"Backup script not found: {script}")
    return _run_subprocess(["bash", script], timeout=300)


# ═══════════════════════════════════════════════
# DEPLOY
# ═══════════════════════════════════════════════


def deploy_service(name: str) -> dict[str, Any]:
    """Deploy a service by name."""
    if not _validate_name(name):
        return _err(f"Invalid service name: '{name}'")
    return _run_subprocess(["docker", "compose", "up", "-d", name], timeout=120)


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
        emoji = {"ok": "✅", "fail": "❌", "warn": "⚠️"}.get(c.get("status", ""), "ℹ️")
        lines.append(f"{emoji} `{c['name']}` — {c.get('detail', '')}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════
# GIT
# ═══════════════════════════════════════════════


def git_status(repo_name: str) -> dict[str, Any]:
    """Get git status for a known repo."""
    if not _validate_name(repo_name):
        return _err(f"Invalid repo name: '{repo_name}'")

    base_path = f"/srv/work/repos/{repo_name}"
    if not os.path.isdir(base_path):
        # Try alternative locations
        alt = f"/root/{repo_name}"
        if os.path.isdir(alt):
            base_path = alt
        else:
            return _err(f"Repo not found at /srv/work/repos/{repo_name}")

    # Get branch, status, and recent commits
    branch_r = _run_subprocess(["git", "branch", "--show-current"], timeout=5, cwd=base_path)
    status_r = _run_subprocess(["git", "status", "--short"], timeout=5, cwd=base_path)
    log_r = _run_subprocess(["git", "log", "--oneline", "-5"], timeout=5, cwd=base_path)

    output = f"📁 *{repo_name}*\n"
    output += f"🌿 Branch: `{branch_r.get('output', '').strip() or 'unknown'}`\n"

    dirty = status_r.get("output", "").strip()
    if dirty:
        lines = dirty.splitlines()
        output += f"📝 *{len(lines)} uncommitted file(s):*\n"
        for line in lines[:10]:
            output += f"  `{line[:60]}`\n"
    else:
        output += "✅ Clean working tree\n"

    if log_r["ok"] and log_r["output"].strip():
        output += f"\n📜 *Recent commits:*\n```\n{log_r['output'].strip()}\n```"

    return _ok(output)


def push_all_repos() -> dict[str, Any]:
    """Push all repos using the push-all-repos script."""
    script_path = "/root/scripts/push-all-repos.sh"
    if not os.path.exists(script_path):
        return _err(f"Push script not found: {script_path}")
    return _run_subprocess(["bash", script_path], timeout=300)


# ═══════════════════════════════════════════════
# CHAIN STATUS
# ═══════════════════════════════════════════════


def chain_status() -> dict[str, Any]:
    """Get LiteIndexer chain sync status from Docker."""
    # Check for running chain indexer containers
    result = _run_subprocess(
        [
            "docker", "ps", "--filter", "name=chain-indexer",
            "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
        ],
        timeout=10,
    )
    if result["ok"] and result["output"].strip():
        return result

    # Fallback: check for any container with 'chain' or 'indexer' in name
    result = _run_subprocess(
        [
            "docker", "ps",
            "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}",
        ],
        timeout=10,
    )
    # Filter for chain-related containers
    lines = ["📡 *Chain Indexer Status*\n"]
    found = False
    for line in result["output"].splitlines():
        if any(kw in line.lower() for kw in ["chain", "indexer", "lighthouse", "heimdall", "bor", "geth", "reth", "lodestar"]):
            lines.append(f"  {line}")
            found = True
    if not found:
        lines.append("  No chain indexer containers detected.")
        lines.append("\n💡 Tip: Run `/search chain` or check with `/docker ps`")
    return _ok("\n".join(lines))


# ═══════════════════════════════════════════════
# ALERTS
# ═══════════════════════════════════════════════


def send_alert(message: str, level: str = "info") -> dict[str, Any]:
    """Dispatch an alert to the configured admin chat via the running bot."""
    # Alerts are sent via the health endpoint or directly through the bot
    # This function creates a JSON payload for the Redis queue
    from app.domains.telegram.adminbot.config import ADMIN_CHAT_ID

    if not message:
        return _err("Message is required")

    # Try to push to Redis queue for the bot to pick up
    try:
        import json

        import redis.asyncio as aioredis

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        redis_password = os.environ.get("REDIS_PASSWORD", "")
        if redis_password and "://" in redis_url and "@" not in redis_url:
            parts = redis_url.split("://", 1)
            redis_url = f"{parts[0]}://:{redis_password}@{parts[1]}"

        # Use sync redis for simplicity here
        import redis as sync_redis

        client = sync_redis.from_url(redis_url)
        payload = json.dumps({
            "level": level.upper(),
            "title": "🔔 Admin Alert",
            "body": message,
            "source": "admin_bot",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        })
        client.rpush("rag:telegram_queue", payload)
        client.close()
        return _ok(f"✅ Alert queued for chat {ADMIN_CHAT_ID}", duration=0.0)
    except (ImportError, ConnectionError, OSError, ValueError) as e:
        return _err(f"Redis unavailable: {e}")
