"""Helper functions for newsletter operations."""

from __future__ import annotations

import re


def get_setting(key: str, default: str = "") -> str:
    """Get a configuration value."""
    from app.config import settings

    return getattr(settings, key, None) or default or ""


def slugify(text: str) -> str:
    """Create a URL-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:200] or "untitled"
