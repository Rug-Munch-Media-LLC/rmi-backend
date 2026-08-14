"""
_ops_core — subprocess primitives + /sh read-only shell allowlist.

Extracted from ``admin_ops`` (degodding 2026-08-14). Contains the
shared ``_run_subprocess`` helper, name validation, and the security
hardening allowlist that gates every ``/sh`` command.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
import time
from typing import Any

logger = logging.getLogger("admin_bot.admin_ops")

# ── /sh ALLOWLIST (security hardening 2026-08-13) ─────────────────────
# P0: replaced the narrow BLOCKLIST with an ALLOWLIST. Only read-only base
# commands may run via /sh — nothing that can mutate the host. Commands with
# subcommand surfaces (docker/systemctl/git) are further restricted to
# read-only subcommands. No shell redirection/pipes/compound statements.
_ALLOWED_SHELL_COMMANDS: frozenset[str] = frozenset(
    {
        # status
        "uptime",
        "uname",
        "hostname",
        "date",
        "who",
        "w",
        "last",
        "lastlog",
        "whoami",
        "id",
        # logs
        "journalctl",
        "tail",
        "head",
        # processes / memory
        "ps",
        "top",
        "free",
        "vmstat",
        # filesystem (read-only)
        "ls",
        "tree",
        "stat",
        "du",
        "df",
        "pwd",
        "find",
        "cat",
        "grep",
        "wc",
        "awk",
        "file",
        # network (read-only)
        "netstat",
        "ss",
        "ip",
        "ping",
        "host",
        "dig",
        "getent",
        "curl",
        # multi-subcommand tools (validated against *_READONLY_SUBCOMMANDS)
        "docker",
        "systemctl",
        "git",
        "docker-compose",
    }
)

# docker subcommands allowed via /sh (read-only only — no run/rm/exec/prune).
_DOCKER_READONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "ps",
        "logs",
        "stats",
        "inspect",
        "images",
        "info",
        "version",
        "top",
        "port",
        "events",
        "volume",
        "network",
        "system",
        "compose",
    }
)
_DOCKER_READONLY_SECONDARY: frozenset[str] = frozenset({"ls", "df", "config", "ps", "logs", "top", "images", "inspect"})

# systemctl subcommands allowed via /sh (read-only only — no start/stop/restart).
_SYSTEMCTL_READONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "list-units",
        "list-services",
        "list-timers",
        "list-sockets",
        "list-machines",
        "list-unit-files",
        "is-active",
        "is-enabled",
        "is-failed",
        "show",
        "cat",
        "help",
    }
)

# git subcommands allowed via /sh (read-only only — no push/checkout/reset).
_GIT_READONLY_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "status",
        "log",
        "diff",
        "show",
        "branch",
        "tag",
        "rev-parse",
        "rev-list",
        "ls-files",
        "ls-tree",
        "remote",
        "describe",
        "name-rev",
        "shortlog",
        "whatchanged",
    }
)

# find(1) arguments that mutate — rejected even though find is allowlisted.
_FIND_MUTATING_ARGS: frozenset[str] = frozenset(
    {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fls", "-fprint", "-fprintf"}
)

# Shell metacharacters that imply redirection / pipes / compound commands.
_SHELL_METACHARS: tuple[str, ...] = ("|", ">", "<", "&", ";", "`", "$", "(", ")", "\n")

# Allowed systemctl actions
_SYSTEMCTL_ACTIONS: frozenset[str] = frozenset({"start", "stop", "restart", "status", "reload", "enable", "disable"})


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
        r = subprocess.run(  # noqa: S603 — allowlist-validated list-args, shell=False (see run_shell)
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


def _shell_allowlist_error(parts: list[str]) -> str | None:
    """Validate a shell command against the /sh read-only allowlist.

    Returns None when allowed, else a human-readable rejection message.
    """
    if not parts:
        return "Empty command"
    base = parts[0]

    if any(char in " ".join(parts) for char in _SHELL_METACHARS):
        return "Shell metacharacters (redirection / pipes / compound) are not allowed"

    if base == "docker":
        if len(parts) < 2 or parts[1] not in _DOCKER_READONLY_SUBCOMMANDS:
            allowed = ", ".join(sorted(_DOCKER_READONLY_SUBCOMMANDS))
            return f"docker: only read-only subcommands allowed ({allowed})"
        if parts[1] in ("volume", "network", "system", "compose") and (
            len(parts) < 3 or parts[2] not in _DOCKER_READONLY_SECONDARY
        ):
            return f"docker {parts[1]}: only read-only subcommands allowed (ls/df/config/ps/logs)"
        return None

    if base == "docker-compose":
        if len(parts) < 2 or parts[1] not in _DOCKER_READONLY_SECONDARY:
            return "docker-compose: only read-only subcommands allowed (ps/logs/config/top/images)"
        return None

    if base == "systemctl":
        if len(parts) < 2 or parts[1] not in _SYSTEMCTL_READONLY_SUBCOMMANDS:
            allowed = ", ".join(sorted(_SYSTEMCTL_READONLY_SUBCOMMANDS))
            return f"systemctl: only read-only subcommands allowed ({allowed})"
        return None

    if base == "git":
        if len(parts) < 2 or parts[1] not in _GIT_READONLY_SUBCOMMANDS:
            allowed = ", ".join(sorted(_GIT_READONLY_SUBCOMMANDS))
            return f"git: only read-only subcommands allowed ({allowed})"
        return None

    if base not in _ALLOWED_SHELL_COMMANDS:
        allowed = ", ".join(sorted(_ALLOWED_SHELL_COMMANDS))
        return f"Command not allowed: '{base}'. Read-only allowlist: {allowed}"

    if base == "find":
        for arg in parts[1:]:
            if arg in _FIND_MUTATING_ARGS:
                return f"find: argument '{arg}' is not allowed (mutates)"

    return None


def run_shell(cmd: str, timeout: int = 30) -> dict[str, Any]:
    """Run a command safely via shlex.split() + shell=False.

    READ-ONLY ALLOWLIST only (security hardening): only whitelisted
    read-only base commands run; docker/systemctl/git are restricted to
    read-only subcommands; redirection/pipes/compound statements are
    rejected. Mutating the host via /sh is no longer possible.
    """
    clean = cmd.strip()
    # capture_output=True already captures stderr — drop the legacy suffix.
    for suffix in (" 2>&1", "2>&1"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)].rstrip()

    try:
        parts = shlex.split(clean)
    except ValueError as exc:
        return _err(f"Invalid command syntax: {exc}")

    err_msg = _shell_allowlist_error(parts)
    if err_msg is not None:
        logger.warning("BLOCKED shell command (allowlist): %s — %s", cmd[:200], err_msg)
        return _err(f"Command blocked: {err_msg}")

    return _run_subprocess(parts, timeout=timeout)
