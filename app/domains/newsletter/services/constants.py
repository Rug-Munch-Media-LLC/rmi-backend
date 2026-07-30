"""Newsletter constants — subscription tiers, fee percentages, etc."""

from __future__ import annotations
from typing import Any


SUBSCRIPTION_TIERS: dict[str, dict[str, Any]] = {
    "free": {"price_usd": 0, "duration_days": 0, "label": "Free"},
    "monthly": {"price_usd": 5, "duration_days": 30, "label": "Monthly ($5)"},
    "annual": {"price_usd": 50, "duration_days": 365, "label": "Annual ($50)"},
    "founding": {"price_usd": 100, "duration_days": 365, "label": "Founding ($100)"},
}

PLATFORM_FEE_PCT = 10.0  # 90/10 split
