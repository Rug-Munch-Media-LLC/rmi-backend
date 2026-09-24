"""Commands the owner may run that ordinary users may not."""

from __future__ import annotations

import shlex

from app.domains.telegram.adminbot.admin_terminal import _ALLOWED_COMMANDS

# Mutating or privileged verbs. Anyone may run the read-only set already in
# _ALLOWED_COMMANDS; only the owner reaches these, and only after an audit entry.
OWNER_ONLY: frozenset[str] = frozenset(
    {
        "systemctl",
        "docker",
        "git",
        "deploy",
        "install",
        "apt",
        "apt-get",
        "dnf",
        "yum",
        "pacman",
        "pip",
        "uv",
        "npm",
        "pnpm",
        "bun",
        "make",
        "bash",
        "sh",
        "python",
        "python3",
        "mv",
        "cp",
        "rm",
        "mkdir",
        "touch",
        "chmod",
        "chown",
        "kill",
        "pkill",
        "reboot",
        "shutdown",
        "ufw",
        "iptables",
        "nft",
        "journalctl",
    }
)


def allowed_for(command: str, owner: bool) -> bool:
    """True when `command` may run, widening the set only for the owner.

    A compound line is checked segment by segment, so `ls; reboot` cannot smuggle
    a privileged verb past the read-only allowlist.
    """
    try:
        parts = shlex.split(command)
    except ValueError:
        return False
    if not parts:
        return False
    for segment in " ".join(parts).replace("&&", ";").replace("|", ";").split(";"):
        words = [w for w in segment.split() if w and not w.startswith("-")]
        if not words:
            continue
        base = words[0].rsplit("/", 1)[-1]
        # Owner-only wins over the read-only list. systemctl/docker/git appear in
        # both, so checking the read-only list first would let ANY allowed user
        # restart services or push code. This ordering is the security boundary.
        if base in OWNER_ONLY:
            if not owner:
                return False
            continue
        if base in _ALLOWED_COMMANDS:
            continue
        return False
    return True
