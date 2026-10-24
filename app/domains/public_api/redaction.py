"""Allowlist projection for public payloads.

A public response is built by naming the fields it may contain. Nothing is
copied wholesale, so a new internal field on the scanner model cannot leak by
being added upstream.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Scalar fields safe for every tier: no wallets, no holders, no evidence blobs.
_SCALARS: tuple[str, ...] = (
    "address",
    "chain",
    "safety_score",
    "risk_level",
    "scanned_at",
    "summary",
)

# Identifier-only lists: names of triggered checks, never their evidence.
_NAME_LISTS: tuple[str, ...] = ("flags", "risk_flags", "checks")


def _flag_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("name"), str):
            names.append(item["name"])
    return names


def public_scan(payload: Mapping[str, Any], *, depth: str) -> dict[str, Any]:
    """Project a cached scan onto the public shape for the caller's depth."""
    projected = {key: payload[key] for key in _SCALARS if key in payload}
    projected["flags"] = [name for key in _NAME_LISTS for name in _flag_names(payload.get(key))]
    if depth != "summary":
        projected["stability"] = payload.get("stability")
    if depth == "full":
        projected["metrics"] = {
            key: value
            for key, value in (payload.get("metrics") or {}).items()
            if isinstance(value, (int, float, str, bool))
        }
    return projected
