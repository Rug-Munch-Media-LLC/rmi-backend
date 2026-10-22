"""
terminal_policy — security policy for the admin bot terminal (a root shell over Telegram).

Single source of truth for three things:

* **tiers** — a base command is not a permission. ``systemctl status`` and
  ``systemctl stop`` share a binary but not a risk; ``docker ps`` and
  ``docker rm -f`` share a binary and can destroy production data.
* **path jail** — ``cd`` and every file target must resolve (realpath, so
  symlinks cannot escape) inside :data:`ROOTS`.
* **child environment** — the bot runs with ``/srv/secrets/*.env`` loaded; the
  shell it spawns must not inherit them.

Pure functions only (no I/O) so the policy is testable without a bot.

Tiers: ``read`` runs freely · ``mutate`` needs a per-invocation confirmation ·
``danger`` needs a typed token · ``deny`` is refused outright.
"""

from __future__ import annotations

import os
import re

READ = "read"
MUTATE = "mutate"
DANGER = "danger"
DENY = "deny"

#: Directories the shell may operate in. Everything else is out of bounds.
ROOTS: tuple[str, ...] = ("/srv/work", "/var/log", "/root/.knowledge")
DEFAULT_CWD: str = "/srv/work"

#: Read paths that must never be returned to a chat window.
SENSITIVE_PREFIXES: tuple[str, ...] = (
    "/srv/secrets",
    "/root/.gnupg",
    "/root/.ssh",
    "/root/.config/gopass",
    "/root/.local/share/gopass",
    "/etc/shadow",
    "/etc/gshadow",
    "/etc/sudoers",
    "/proc/1/",
    "/backups/.secrets",
)
SENSITIVE_HINTS: tuple[str, ...] = ("/environ", ".env", ".pem", "id_rsa", "id_ed25519", "credentials")

#: Refused even if a future edit re-adds them to the bot allowlist.
HARD_DENY: frozenset[str] = frozenset(
    {
        "rm",
        "dd",
        "mkfs",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "find",
        "sed",
        "awk",
        "tee",
        "xargs",
        "bash",
        "sh",
        "zsh",
        "python",
        "python3",
        "perl",
        "ruby",
        "node",
        "env",
        "sudo",
        "su",
        "chmod",
        "chown",
        "chattr",
        "mount",
        "umount",
        "iptables",
        "nft",
        "systemd-run",
        "nsenter",
        "unshare",
        "kill",
        "pkill",
        "killall",
        "rsync",
        "scp",
        "nc",
        "socat",
        "tar",
        "crontab",
        "at",
    }
)

READ_BINARIES: frozenset[str] = frozenset(
    {
        "ls",
        "pwd",
        "df",
        "free",
        "ps",
        "cat",
        "head",
        "tail",
        "grep",
        "uptime",
        "who",
        "w",
        "last",
        "netstat",
        "ss",
        "du",
        "lsof",
        "stat",
        "tree",
        "top",
        "htop",
        "ping",
        "journalctl",
        "docker",
        "systemctl",
        "git",
        "ip",
        "docker-compose",
        "curl",
    }
)
MUTATE_BINARIES: frozenset[str] = frozenset({"mkdir", "touch", "cp", "mv"})

#: Per-binary sub-command tiers. Unknown sub-commands fall back to ``_DEFAULT``.
_SUB: dict[str, tuple[dict[str, str], str]] = {
    "docker": (
        {
            "ps": READ,
            "images": READ,
            "logs": READ,
            "inspect": READ,
            "stats": READ,
            "version": READ,
            "info": READ,
            "port": READ,
            "history": READ,
            "diff": READ,
            "start": MUTATE,
            "restart": MUTATE,
            "pause": MUTATE,
            "unpause": MUTATE,
            "stop": DANGER,
            "kill": DANGER,
            "rm": DANGER,
            "rmi": DANGER,
            "prune": DANGER,
            "system": DANGER,
            "exec": DANGER,
            "run": DANGER,
            "cp": DANGER,
        },
        DANGER,
    ),
    "docker-compose": ({"ps": READ, "logs": READ, "config": READ}, DANGER),
    "systemctl": (
        {
            "status": READ,
            "show": READ,
            "cat": READ,
            "is-active": READ,
            "is-enabled": READ,
            "is-failed": READ,
            "list-units": READ,
            "list-unit-files": READ,
            "list-timers": READ,
            "start": MUTATE,
            "restart": MUTATE,
            "reload": MUTATE,
            "try-restart": MUTATE,
            "stop": DANGER,
            "disable": DANGER,
            "mask": DANGER,
            "kill": DANGER,
            "poweroff": DANGER,
            "reboot": DANGER,
            "isolate": DANGER,
        },
        DANGER,
    ),
    "git": (
        {
            "status": READ,
            "log": READ,
            "diff": READ,
            "show": READ,
            "branch": READ,
            "rev-parse": READ,
            "remote": READ,
            "ls-files": READ,
            "describe": READ,
            "blame": READ,
            "shortlog": READ,
            "tag": READ,
            "add": MUTATE,
            "commit": MUTATE,
            "fetch": MUTATE,
            "pull": MUTATE,
            "stash": MUTATE,
            "push": DANGER,
            "reset": DANGER,
            "clean": DANGER,
            "rebase": DANGER,
            "merge": DANGER,
            "checkout": DANGER,
            "switch": DANGER,
            "gc": DANGER,
            "filter-branch": DANGER,
            "config": DANGER,
        },
        DANGER,
    ),
    "ip": (
        {"addr": READ, "link": READ, "route": READ, "neigh": READ, "show": READ},
        DANGER,
    ),
    "journalctl": ({"--vacuum-size": DANGER, "--vacuum-time": DANGER, "--rotate": DANGER, "--flush": DANGER}, READ),
    "curl": (
        {
            "-d": DANGER,
            "--data": DANGER,
            "-F": DANGER,
            "--form": DANGER,
            "-T": DANGER,
            "--upload-file": DANGER,
            "-o": DANGER,
            "--output": DANGER,
            "-O": DANGER,
            "--remote-name": DANGER,
            "-K": DANGER,
            "--config": DANGER,
        },
        MUTATE,
    ),
}

#: Binaries that write files: the target must resolve inside the jail.
_WRITE_TARGET_BINARIES: frozenset[str] = frozenset({"mkdir", "touch", "cp", "mv"})

#: argv[0] may only be a bare name or live in a system binary directory.
_SYSTEM_BIN_DIRS: tuple[str, ...] = ("/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/", "/usr/local/bin/")

_META = re.compile(r"[;&|$`<>\n\r\t]")
_ENV_ALLOW: frozenset[str] = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "TERM"})


def _base(argv: list[str]) -> str | None:
    """Return the normalised binary name, or None when argv[0] is not a system binary."""
    raw = argv[0]
    if "/" in raw and not raw.startswith(_SYSTEM_BIN_DIRS):
        return None
    return os.path.basename(raw)


def is_sensitive(text: str) -> bool:
    """True when a token points at a secret-bearing path."""
    low = text.lower()
    return low.startswith(SENSITIVE_PREFIXES) or any(h in low for h in SENSITIVE_HINTS)


def resolve_in_jail(path: str, cwd: str) -> str | None:
    """Resolve ``path`` against ``cwd``; return the realpath when inside ROOTS, else None."""
    if not path or "\x00" in path or _META.search(path):
        return None
    target = path if os.path.isabs(path) else os.path.join(cwd, path)
    real = os.path.realpath(target)
    for root in ROOTS:
        root_real = os.path.realpath(root)
        if real == root_real or real.startswith(root_real + os.sep):
            return real
    return None


def _sub_tier(base: str, argv: list[str]) -> tuple[str, str]:
    table, default = _SUB[base]
    for token in argv[1:4]:
        if token in table:
            return table[token], f"{base} {token}"
    return default, f"{base} (unclassified sub-command)"


def classify(argv: list[str], cwd: str = DEFAULT_CWD) -> tuple[str, str]:
    """Classify one argv into (tier, reason). Refuses anything not explicitly known."""
    if not argv:
        return DENY, "empty command"
    base = _base(argv)
    if base is None:
        return DENY, "binary outside system dirs"
    if base in HARD_DENY:
        return DENY, f"{base} is permanently refused"
    if any(is_sensitive(a) for a in argv):
        return DENY, "sensitive path"
    if base in _WRITE_TARGET_BINARIES and resolve_in_jail(argv[-1], cwd) is None:
        return DENY, f"{base} target outside the path jail"
    if base in _SUB:
        return _sub_tier(base, argv)
    if base in READ_BINARIES:
        return READ, base
    if base in MUTATE_BINARIES:
        return MUTATE, base
    return DENY, f"{base} is not in the allowlist"


def scrubbed_env() -> dict[str, str]:
    """Return the only environment the terminal child is allowed to see."""
    env = {k: v for k, v in os.environ.items() if k in _ENV_ALLOW}
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    env["HOME"] = "/root"
    env["TERM"] = "dumb"
    return env


def allowed_binaries() -> frozenset[str]:
    """Every binary the terminal may run at all (used by the help text / audit)."""
    return frozenset(READ_BINARIES | MUTATE_BINARIES) - HARD_DENY
