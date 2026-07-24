"""

Deep Intelligence Engine — Shared Data Models
===============================================
Type aliases and result model hints for the deep intelligence system.
"""

from __future__ import annotations

from typing import Any

# ── Type aliases ───────────────────────────────────────────────────────────

JsonDict = dict[str, Any]
RiskFlags = list[str]
RiskLevel = str  # "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "MINIMAL" | "UNKNOWN"

# ── Result structure hints (not enforced at runtime, but useful for docs) ──

__all__ = [
    "JsonDict",
    "RiskFlags",
    "RiskLevel",
]
