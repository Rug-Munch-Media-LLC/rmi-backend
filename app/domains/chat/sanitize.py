"""Input sanitization — strip control bytes and cap length."""

from __future__ import annotations

import re

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_control(text: str, limit: int) -> str:
    """Replace control characters with spaces and cap to ``limit`` chars."""
    return _CONTROL_RE.sub(" ", text or "")[:limit]
