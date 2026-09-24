"""Map a plain-language message onto an existing bot command."""

from __future__ import annotations

import re

NL = chr(10)

# Ordered: the first matching pattern wins, so put the specific phrases first.
ROUTES: tuple[tuple[str, str], ...] = (
    (r"\b(restart|reboot)\b.*\b(\w+)", "svc {1} restart"),
    (r"\b(stop|halt)\b.*\b(\w+)", "svc {1} stop"),
    (r"\b(start|run)\b.*\b(\w+)", "svc {1} start"),
    (r"\b(logs?|journal)\b.*\b(\w+)", "svc {1} logs"),
    (r"\b(deploy|release)\b", "deploy"),
    (r"\b(disks?|storage)\b", "disk"),
    (r"\b(memory|ram)\b", "mem"),
    (r"\b(docker|containers?)\b", "dps"),
    (r"\b(git)\b", "git"),
    (r"\b(models?|engine|llm)\b", "__models__"),
    (r"\b(health|status|how.*(going|it))\b", "status"),
    (r"\b(uptime|load)\b", "uptime"),
    (r"\b(alerts?|warnings?)\b", "alerts"),
    (r"\b(backups?)\b", "admin_backup_status"),
)


# Intents any allowed user may run. Everything else is owner-only.
OPEN_INTENTS: frozenset[str] = frozenset({"status", "disk", "mem", "dps", "uptime", "alerts", "__models__"})


def classify(text: str) -> str | None:
    """Return the command to run for `text`, or None if it is not a request.

    Only phrases in ROUTES are translated. Free-form questions are left to the
    assistant path rather than guessed at a server command.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return None
    for pattern, template in ROUTES:
        match = re.search(pattern, lowered)
        if not match:
            continue
        groups = [g for g in match.groups() if g]
        return template.format(*groups) if groups else template
    return None
