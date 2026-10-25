"""A policy provider imported by the seam tests via RMI_RISK_POLICY."""

from __future__ import annotations

from typing import Any


def default_policy() -> dict[str, Any]:
    return {
        "name": "fixture-policy",
        "rules": (("noise", 1, "noise", "OTHER", (((("noise",),), lambda value: value is True),)),),
        "hard_floor_flags": (),
        "bands": {"critical": 90, "high": 60, "medium": 30},
        "constants": {},
        "groups": {"noise": "OTHER"},
    }
