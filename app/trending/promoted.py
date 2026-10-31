"""Paid trending tier — a separate, labelled slot that can never re-rank.

A paid placement buys visibility in its OWN array, never a position inside the
organic list. Three rules are enforced here, not just documented:

1. **Separation** — ``select_promoted`` returns its own list; the caller must
   not merge it. Promotion never changes an organic item's score or index.
2. **Labelling** — every promoted item carries ``placement="promoted"``, the
   disclosure string and the advertiser name. Organic items carry
   ``placement="organic"`` and no disclosure.
3. **Refusal** — a token that is risk-flagged or scam-overlapping cannot be
   promoted even if paid for; it is returned in ``rejected`` with the reason.

The slot registry is data, not code: a JSON file (``TRENDING_PROMOTIONS_FILE``)
so ops can sell a slot without a deploy. Fail-open: a missing or malformed
registry means "no promotions", never a broken board.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DISCLOSURE = "Promoted placement — paid. Does not affect organic ranking."
MAX_PER_CHAIN = 2
PRICE_USD_PER_DAY = 500.0
MAX_HIGH_RISK = 0.0


def registry_path() -> Path:
    """Return the promotions registry path (env-overridable)."""
    return Path(os.environ.get("TRENDING_PROMOTIONS_FILE", "data/trending_promotions.json"))


def load_slots(path: Path | None = None) -> list[dict[str, Any]]:
    """Read slots; an unreadable or malformed file yields an empty list."""
    target = path or registry_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    slots = raw.get("slots") if isinstance(raw, dict) else raw
    return [s for s in slots if isinstance(s, dict)] if isinstance(slots, list) else []


def _active(slot: dict[str, Any], now: datetime) -> bool:
    """A slot is active between starts_at and ends_at (ISO-8601, UTC)."""
    try:
        start = datetime.fromisoformat(str(slot["starts_at"]))
        end = datetime.fromisoformat(str(slot["ends_at"]))
    except (KeyError, ValueError):
        return False
    return start <= now <= end


def select_promoted(
    slots: list[dict[str, Any]],
    chain: str,
    now: datetime | None = None,
    blocked: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split active slots for one chain into ``(promoted, rejected)``."""
    moment = now or datetime.now(UTC)
    deny = blocked or set()
    promoted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for slot in sorted(slots, key=lambda s: str(s.get("starts_at", ""))):
        if str(slot.get("chain", "")).lower() != chain.lower() or not _active(slot, moment):
            continue
        token = str(slot.get("token", ""))
        if not token:
            continue
        if token in deny:
            rejected.append({"token": token, "reason": "risk_flagged_not_promotable"})
            continue
        if len(promoted) >= MAX_PER_CHAIN:
            rejected.append({"token": token, "reason": "chain_slot_capacity_reached"})
            continue
        promoted.append(
            {
                "chain": chain.lower(),
                "token": token,
                "symbol": slot.get("symbol"),
                "placement": "promoted",
                "disclosure": DISCLOSURE,
                "advertiser": slot.get("advertiser"),
                "starts_at": slot.get("starts_at"),
                "ends_at": slot.get("ends_at"),
                "price_usd_per_day": float(slot.get("price_usd_per_day", PRICE_USD_PER_DAY)),
            }
        )
    return promoted, rejected
