"""Partner/campaign allowlist for attribution ingest.

Allowlist is configuration, never hardcoded: ``POLY_ATTRIBUTION_ALLOWLIST``
holds JSON ``{"<partner>": {"campaigns": ["..."], "key_hash": "<sha256>"}}``.
An empty or malformed allowlist denies everything (fail closed).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Final

SURFACES: Final[frozenset[str]] = frozenset({"content", "scan", "bot", "frontend"})
EVENTS: Final[frozenset[str]] = frozenset({"view", "click", "signup", "order"})


@dataclass(frozen=True, slots=True)
class PartnerRule:
    """Allowed campaigns + optional hashed partner key for one partner."""

    campaigns: frozenset[str]
    key_hash: str | None


def load_allowlist() -> dict[str, PartnerRule]:
    """Parse the allowlist env var. Any failure yields an empty (deny-all) map."""
    raw = os.getenv("POLY_ATTRIBUTION_ALLOWLIST", "")
    if not raw:
        return {}
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    rules: dict[str, PartnerRule] = {}
    for partner, cfg in parsed.items():
        if not isinstance(partner, str) or not isinstance(cfg, dict):
            continue
        campaigns = cfg.get("campaigns", [])
        if not isinstance(campaigns, list):
            continue
        key_hash = cfg.get("key_hash")
        rules[partner] = PartnerRule(
            campaigns=frozenset(str(c) for c in campaigns),
            key_hash=key_hash if isinstance(key_hash, str) else None,
        )
    return rules


def is_allowed(partner: str, campaign: str) -> bool:
    """True only when the partner is listed and the campaign is listed."""
    rule = load_allowlist().get(partner)
    return rule is not None and campaign in rule.campaigns
