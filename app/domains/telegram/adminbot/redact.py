"""Value redaction — redact known secret values + key patterns from outbound text.

Defense-in-depth for ANY text the bot sends (terminal output, engine results):
the owner-only DM is still a chat history, and secrets must never land there.

Two layers:
  1. Value layer — literal values loaded once from /srv/secrets/agent_keys.env
     plus every sensitive-looking key in backend.env (names matching
     ``SECRET|TOKEN|KEY|PASSWORD|HASH|PASS``). Never logged, never printed.
  2. Pattern layer — common key formats (sk-, ghp_, github_pat_, hf_, gsk_,
     AIza, AKIA, xox*, Telegram bot tokens, JWTs).

``scrub(text)`` is pure given the loaded value set; ``load_secret_values``
accepts explicit paths so tests can use tmp files. No monkeypatching.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger("admin_bot.redact")

AGENT_KEYS_PATH = "/srv/secrets/agent_keys.env"
BACKEND_ENV_PATH = "/srv/secrets/backend.env"

_SENSITIVE_KEY_RE = re.compile(r"SECRET|TOKEN|PASSWORD|PASS|PRIVATE|HASH", re.IGNORECASE)
_MIN_VALUE_LEN = 8

_PATTERN_SCRUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-\.]{16,}"), "[REDACTED]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"hf_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"gsk_[A-Za-z0-9]{20,}"), "[REDACTED]"),
    (re.compile(r"xox[abps]-[A-Za-z0-9\-]{10,}"), "[REDACTED]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED]"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{30,}"), "[REDACTED]"),
    (re.compile(r"bot\d{6,}:[A-Za-z0-9_\-]{30,}"), "bot<REDACTED>"),
    (
        re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
        "[REDACTED-JWT]",
    ),
)

_values_cache: tuple[str, ...] | None = None


def _parse_env_file(path: Path, sensitive_names_only: bool) -> list[str]:
    """Extract candidate secret VALUES from an env file (never logged)."""
    values: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if len(value) < _MIN_VALUE_LEN:
            continue
        if sensitive_names_only and not _SENSITIVE_KEY_RE.search(name):
            continue
        values.append(value)
    return values


def load_secret_values(
    agent_keys_path: str = AGENT_KEYS_PATH,
    backend_env_path: str = BACKEND_ENV_PATH,
) -> tuple[str, ...]:
    """Load secret values (agent_keys: all; backend.env: sensitive names only)."""
    values = _parse_env_file(Path(agent_keys_path), sensitive_names_only=False)
    values += _parse_env_file(Path(backend_env_path), sensitive_names_only=True)
    # Longest first so overlapping values redact cleanly.
    return tuple(sorted(set(values), key=len, reverse=True))


def _cached_values() -> tuple[str, ...]:
    global _values_cache
    if _values_cache is None:
        _values_cache = load_secret_values()
        logger.debug("secrets scrub: %d value(s) loaded", len(_values_cache))
    return _values_cache


def scrub(text: str) -> str:
    """Redact known secret values + key patterns. Never raises."""
    if not text:
        return text
    out = text
    try:
        for value in _cached_values():
            if value in out:
                out = out.replace(value, "[REDACTED]")
    except Exception as exc:  # fail-open: pattern layer still applies
        logger.debug("secrets scrub value layer failed: %s", exc)
    for pattern, replacement in _PATTERN_SCRUBS:
        out = pattern.sub(replacement, out)
    return out
