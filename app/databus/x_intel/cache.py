"""Influencer-registry cache loader for the X/CT intelligence pipeline.

Reads the cached registry file (app/databus/influential_accounts.json) and
returns a normalized account list. Degrades gracefully to [] when the file
is absent or malformed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.core.logging import get_logger

logger = get_logger("x_intel")

_INFLUENTIAL_ACCOUNTS_FILE = Path(__file__).resolve().parent.parent / "influential_accounts.json"


def load_influential_accounts() -> list[dict[str, Any]]:
    """Load the influencer registry. Returns [] when absent/malformed."""
    try:
        if not _INFLUENTIAL_ACCOUNTS_FILE.exists():
            return []
        data = json.loads(_INFLUENTIAL_ACCOUNTS_FILE.read_text(encoding="utf-8"))
        accounts = data.get("accounts", []) if isinstance(data, dict) else []
        return [a for a in accounts if isinstance(a, dict) and a.get("handle")]
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"influential_accounts.json load failed: {e}")
        return []
