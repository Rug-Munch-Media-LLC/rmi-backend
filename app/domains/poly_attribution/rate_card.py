"""Partner rate card loader — the ONLY source for earnings figures.

``POLY_ATTRIBUTION_RATES`` holds JSON keyed by partner, e.g.
``{"acme": {"revshare_pct": 40, "cpa": {"signup": 2.5}}}``. No card means
no estimate — we never invent a rate.
"""

from __future__ import annotations

import json
import os
from typing import Any


def load_rate_card(partner: str) -> dict[str, Any] | None:
    """Return the configured rate card for ``partner``, or None if absent/invalid."""
    raw = os.getenv("POLY_ATTRIBUTION_RATES", "")
    if not raw:
        return None
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    card = parsed.get(partner)
    return card if isinstance(card, dict) else None
