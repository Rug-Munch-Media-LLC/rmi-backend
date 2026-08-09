"""
admin_ops — fleet operations.

Shell execution, Docker, systemd, backup, deploy, disk, memory,
git, push, chain status, and alert dispatch.
"""

from __future__ import annotations

import json
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
_SYSTEMCTL_ACTIONS: frozenset[str] = frozenset({"start", "stop", "restart", "status", "reload", "enable", "disable"})

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
# BACKUP
# ═══════════════════════════════════════════════


_BACKUP_SENTINEL = "/backups/.last-successful-backup"
_GIT_BACKUP_SENTINEL = "/backups/.last-successful-backup-all"

# Per-type backup output directories (scanned for newest artifact mtime).
_BACKUP_TYPE_DIRS: dict[str, str] = {
    "postgres": "/backups/postgres",
    "clickhouse": "/backups/clickhouse",
    "qdrant": "/backups/qdrant",
    "rag": "/backups/rag",
    "app-postgres": "/backups/app-postgres",
    "chain-indexer": "/backups/chain-indexer",
    "git": "/backups/git",
}

# Per-type backup trigger scripts (existing fleet scripts, not fabricated).
_BACKUP_SCRIPTS: dict[str, str] = {
    "full": "/root/scripts/db-backup-all.sh",
    "db": "/root/scripts/db-backup-all.sh",
    "postgres": "/root/scripts/backup-app-postgres.sh",
    "clickhouse": "/root/scripts/db-backup-all.sh",
    "qdrant": "/root/scripts/backup-qdrant.sh",
    "rag": "/root/scripts/backup-rag.sh",
    "chain-indexer": "/root/scripts/backup-chain-indexer.sh",
    "git": "/root/scripts/backup-git.sh",
    "verify": "/root/scripts/backup-verify-all.sh",
}

_B2_VERIFY_SCRIPT = "/root/scripts/backup-verify-all.sh"


def trigger_backup(btype: str = "full") -> dict[str, Any]:
    """Trigger a backup script."""
    script = _BACKUP_SCRIPTS.get(btype)
    if not script or not os.path.exists(script):
        return _err(f"Backup type '{btype}' unknown or script not found: {script}")
    return _run_subprocess(["bash", script], timeout=300)


def _newest_mtime(path: str) -> float | None:
    """Newest mtime of any regular file under a backup dir (or None)."""
    if not os.path.isdir(path):
        return None
    newest: float | None = None
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                if name.startswith("."):
                    continue
                full = os.path.join(root, name)
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    continue
                newest = mtime if newest is None else max(newest, mtime)
    except OSError:
        return newest
    return newest


def backup_status() -> dict[str, Any]:
    """Last successful backup per type: newest artifact mtime + age emoji.

    Also includes the overall sentinel age. Pure data + formatting helpers
    are separated for testability.
    """
    t0 = time.time()
    now = time.time()
    types: list[dict[str, Any]] = []
    for name, path in _BACKUP_TYPE_DIRS.items():
        mtime = _newest_mtime(path)
        if mtime is None:
            types.append({"name": name, "path": path, "ago_h": None, "ts": None, "level": "🔴"})
            continue
        ago_h = (now - mtime) / 3600.0
        level = "🟢" if ago_h < 36 else "🟡" if ago_h < 7 * 24 else "🔴"
        ts = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(mtime))
        types.append({"name": name, "path": path, "ago_h": ago_h, "ts": ts, "level": level})

    overall = _sentinel_age_hours(_BACKUP_SENTINEL)
    overall_level = (
        "🟢" if overall is not None and overall < 36 else "🟡" if overall is not None and overall < 7 * 24 else "🔴"
    )
    return {
        "ok": True,
        "types": types,
        "overall_hours": overall,
        "overall_level": overall_level,
        "duration": time.time() - t0,
    }


def _sentinel_age_hours(path: str) -> float | None:
    """Age in hours of a unix-ts sentinel file, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            ts = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return (time.time() - ts) / 3600.0


def format_backup_status(result: dict[str, Any]) -> str:
    """Render the per-type backup status card."""
    lines = ["📦 *Backup Status*\n"]
    overall = result.get("overall_hours")
    if overall is not None:
        lines.append(f"{result.get('overall_level', '🟡')} Overall: last success {overall:.1f}h ago")
    else:
        lines.append("🔴 Overall: no success sentinel")
    lines.append("")
    lines.append("*Per type (newest artifact):*")
    for t in result.get("types", []):
        if t.get("ts"):
            lines.append(f"  {t.get('level', '🟡')} `{t['name']}` — {t['ts']} ({t['ago_h']:.1f}h ago)")
        else:
            lines.append(f"  🔴 `{t['name']}` — no data")
    lines.append("\n💡 Run `/backup_run <type>` to trigger (postgres|clickhouse|qdrant|rag|chain-indexer|git|full).")
    return "\n".join(lines)


def backup_verify() -> dict[str, Any]:
    """Check B2 sync status: remote reachability + recent sync evidence.

    Runs the fleet ``backup-verify-all.sh`` if present, otherwise falls
    back to a lightweight ``rclone lsd b2-crypt:`` reachability probe and
    the b2-sync log freshness.
    """
    t0 = time.time()
    if os.path.exists(_B2_VERIFY_SCRIPT):
        r = _run_subprocess(["bash", _B2_VERIFY_SCRIPT, "--quiet"], timeout=120)
        return {**r, "duration": time.time() - t0}

    # Fallback: rclone reachability + sync log freshness.
    probe = _run_subprocess(["rclone", "lsd", "b2-crypt:"], timeout=30)
    lines = []
    if probe["ok"]:
        lines.append("✅ B2 remote `b2-crypt:` reachable")
    else:
        lines.append(f"❌ B2 remote `b2-crypt:` unreachable: {probe.get('error', '')[:200]}")
    sync_log = "/backups/b2-sync.log"
    if os.path.exists(sync_log):
        try:
            mtime = os.path.getmtime(sync_log)
            ago_h = (time.time() - mtime) / 3600.0
            lines.append(f"{'🟢' if ago_h < 48 else '🟡'} Last B2 sync log: {ago_h:.1f}h ago")
        except OSError:
            lines.append("🟡 B2 sync log unreadable")
    return _ok("\n".join(lines) if lines else "No B2 verification available", time.time() - t0)


def backup_report() -> dict[str, Any]:
    """Last successful backup timestamps + recent backup artifacts on disk."""
    t0 = time.time()

    def _sentinel_ts(path: str) -> int | None:
        try:
            with open(path, encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    now = time.time()
    sentinels: list[tuple[str, int | None]] = [
        ("App/DB backup", _sentinel_ts(_BACKUP_SENTINEL)),
        ("Git push backup", _sentinel_ts(_GIT_BACKUP_SENTINEL)),
    ]
    lines = ["📦 *Backup Report*\n"]
    for label, ts in sentinels:
        if ts:
            ago_h = (now - ts) / 3600.0
            emoji = "🟢" if ago_h < 36 else "🟡" if ago_h < 7 * 24 else "🔴"
            lines.append(
                f"{emoji} {label}: `{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ts))}` ({ago_h:.1f}h ago)"
            )
        else:
            lines.append(f"🔴 {label}: no sentinel found")

    # Recent backup artifacts (sorted newest first).
    backup_dir = "/backups"
    recent: list[tuple[float, str]] = []
    if os.path.isdir(backup_dir):
        try:
            for name in os.listdir(backup_dir):
                full = os.path.join(backup_dir, name)
                if not os.path.isfile(full) or name.startswith("."):
                    continue
                try:
                    recent.append((os.path.getmtime(full), name))
                except OSError:
                    continue
        except OSError:
            pass
    recent.sort(reverse=True)
    if recent:
        lines.append("\n*Recent backup artifacts:*")
        for mtime, name in recent[:8]:
            lines.append(f"  • `{name[:48]}` {time.strftime('%m-%d %H:%M', time.gmtime(mtime))}")
    else:
        lines.append("\nNo recent backup artifacts found in /backups.")

    lines.append("\n💡 Run `/backup run` to trigger a full backup.")
    return _ok("\n".join(lines), time.time() - t0)


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
            "docker",
            "ps",
            "--filter",
            "name=chain-indexer",
            "--format",
            "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
        ],
        timeout=10,
    )
    if result["ok"] and result["output"].strip():
        return result

    # Fallback: check for any container with 'chain' or 'indexer' in name
    result = _run_subprocess(
        [
            "docker",
            "ps",
            "--format",
            "table {{.Names}}\t{{.Status}}\t{{.Image}}",
        ],
        timeout=10,
    )
    # Filter for chain-related containers
    lines = ["📡 *Chain Indexer Status*\n"]
    found = False
    for line in result["output"].splitlines():
        if any(
            kw in line.lower()
            for kw in ["chain", "indexer", "lighthouse", "heimdall", "bor", "geth", "reth", "lodestar"]
        ):
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

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        redis_password = os.environ.get("REDIS_PASSWORD", "")
        if not redis_password:
            for secret_file in ("/run/secrets/redis_password", "/srv/secrets/redis_password"):
                try:
                    with open(secret_file) as f:
                        redis_password = f.read().strip()
                        break
                except (FileNotFoundError, OSError):
                    continue

        if redis_password and "://" in redis_url and "@" not in redis_url:
            parts = redis_url.split("://", 1)
            redis_url = f"{parts[0]}://:{redis_password}@{parts[1]}"

        import redis as sync_redis

        # Force RESP2 protocol to avoid HELLO/AUTH ordering issue
        # with redis-py 8.x + DragonflyDB/Redis 7.x password auth.
        client = sync_redis.from_url(redis_url, protocol=2)
        payload = json.dumps(
            {
                "level": level.upper(),
                "title": "🔔 Admin Alert",
                "body": message,
                "source": "admin_bot",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            }
        )
        client.rpush("rag:telegram_queue", payload)
        client.close()
        return _ok(f"✅ Alert queued for chat {ADMIN_CHAT_ID}", duration=0.0)
    except (ImportError, ConnectionError, OSError, ValueError) as e:
        return _err(f"Redis unavailable: {e}")


# ═══════════════════════════════════════════════
# RAG STATS
# ═══════════════════════════════════════════════


def rag_stats() -> dict[str, Any]:
    """Get RAG collection sizes and embedder status from the backend."""
    try:
        import httpx as sync_httpx

        from app.domains.telegram.adminbot.config import RMI_BACKEND_URL

        r = sync_httpx.get(
            f"{RMI_BACKEND_URL}/api/v1/rag/v2/stats",
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return _ok(json.dumps(data))
        return _err(f"RAG stats returned {r.status_code}")
    except (ImportError, ConnectionError, OSError, ValueError) as e:
        return _err(str(e))


# ═══════════════════════════════════════════════
# NEWS FEED
# ═══════════════════════════════════════════════


def news_feed(limit: int = 5) -> dict[str, Any]:
    """Get latest rated news articles from the backend news feed."""
    try:
        import httpx as sync_httpx

        from app.domains.telegram.adminbot.config import RMI_BACKEND_URL

        r = sync_httpx.get(
            f"{RMI_BACKEND_URL}/api/v1/internal/news-feed",
            params={"channel": "news", "limit": limit, "min_score": 0},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return _ok(json.dumps(data.get("items", [])))
        return _err(f"News feed returned {r.status_code}")
    except (ImportError, ConnectionError, OSError, ValueError) as e:
        return _err(str(e))


# ═══════════════════════════════════════════════
# SCANNER STATS
# ═══════════════════════════════════════════════


def scan_stats() -> dict[str, Any]:
    """Get scanner usage and own_data vs external breakdown."""
    try:
        import httpx as sync_httpx

        from app.domains.telegram.adminbot.config import RMI_BACKEND_URL

        metrics = sync_httpx.get(
            f"{RMI_BACKEND_URL}/api/v1/scanner/metrics",
            timeout=10,
        )
        sufficiency = sync_httpx.get(
            f"{RMI_BACKEND_URL}/api/v1/scanner/sufficiency",
            timeout=10,
        )

        result = {}
        if metrics.status_code == 200:
            result["metrics"] = metrics.json()
        if sufficiency.status_code == 200:
            result["sufficiency"] = sufficiency.json()

        return _ok(json.dumps(result)) if result else _err("No scanner data available")
    except (ImportError, ConnectionError, OSError, ValueError) as e:
        return _err(str(e))


# ═══════════════════════════════════════════════
# INDEXER STATUS (chain-by-chain)
# ═══════════════════════════════════════════════


def indexer_status() -> dict[str, Any]:
    """Get LiteIndexer chain-by-chain status from Docker, organized by chain."""
    # Check docker ps for chain-indexer containers with detailed info
    result = _run_subprocess(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            "name=chain-indexer",
            "--format",
            "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.Ports}}",
        ],
        timeout=10,
    )

    if result["ok"] and result["output"].strip():
        lines = ["📡 *Chain Indexer Status*\n"]
        for line in result["output"].splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                name, status, image = parts[0], parts[1], parts[2]
                ports = parts[3] if len(parts) > 3 else ""
                status_emoji = "✅" if "Up" in status else "🔴" if "Exited" in status else "⏳"
                name_clean = name.replace("chain-indexer-", "")
                lines.append(f"{status_emoji} `{name_clean}`")
                lines.append(f"   Status: {status}")
                if image != "<none>":
                    lines.append(f"   Image: {image}")
                if ports:
                    lines.append(f"   Ports: {ports}")
                lines.append("")
        if len(lines) <= 2:
            lines.append("  No chain-indexer containers found (docker ps -a).")
        return _ok("\n".join(lines))

    # Fallback: check all running containers for indexer/chain names
    result = _run_subprocess(
        [
            "docker",
            "ps",
            "--format",
            "table {{.Names}}\t{{.Status}}\t{{.Image}}",
        ],
        timeout=10,
    )
    lines = ["📡 *Chain Indexer Status*\n"]
    found = False
    for line in result["output"].splitlines():
        if any(
            kw in line.lower()
            for kw in ["chain", "indexer", "lighthouse", "geth", "reth", "heimdall", "bor", "lodestar"]
        ):
            lines.append(f"  {line}")
            found = True
    if not found:
        lines.append("  No chain indexer containers detected.")
    return _ok("\n".join(lines))
