"""Handle registry: trust weight + class per X account.

`data/x_sources.json` is the registry of record (written by `x_registry.py` after
verifying every handle); SEEDS is the same set compiled in, so the lane never
hard-fails on a missing or half-written file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

REGISTRY = Path(
    os.environ.get(
        "X_SOURCES_JSON", Path(__file__).resolve().parents[2] / "data" / "x_sources.json"
    )
)


@dataclass(frozen=True, slots=True)
class Source:
    """One X account plus the weight it contributes to ranking."""

    handle: str
    klass: str
    trust: float


#: Verified crypto news/security primaries. trust mirrors news_fallback.classify().
SEEDS: tuple[Source, ...] = (
    Source("bubblemaps", "analytics", 0.82),
    Source("lookonchain", "analytics", 0.85),
    Source("gmgnai", "analytics", 0.82),
    Source("arkham", "analytics", 0.85),
    Source("spotonchain", "analytics", 0.85),
    Source("whale_alert", "analytics", 0.8),
    Source("OnchainLens", "analytics", 0.82),
    Source("DefiLlama", "analytics", 0.85),
    Source("zachxbt", "security", 0.9),
    Source("realScamSniffer", "security", 0.9),
    Source("PeckShieldAlert", "security", 0.92),
    Source("CertiKAlert", "security", 0.92),
    Source("SlowMist_Team", "security", 0.92),
    Source("BlockSecTeam", "security", 0.9),
    Source("SEAL_Org", "security", 0.88),
    Source("rektnews", "news", 0.85),
    Source("CoinDesk", "news", 0.85),
    Source("TheBlock", "news", 0.9),
    Source("WuBlockchain", "news", 0.88),
    Source("Cointelegraph", "news", 0.85),
    Source("solana", "project", 0.8),
    Source("ethereum", "project", 0.8),
)


def _from_row(row: dict[str, object]) -> Source | None:
    """Accept {"handle","class","trust"} or {"handle","category","quality_weight"}."""
    handle = str(row.get("handle") or "").strip().lstrip("@")
    if not handle:
        return None
    klass = str(row.get("class") or row.get("category") or "news")
    trust = row.get("trust", row.get("quality_weight", 0.75))
    try:
        return Source(handle, klass, float(trust))
    except (TypeError, ValueError):
        return None


def load_registry() -> list[Source]:
    """Registry file first, SEEDS second, deduped by lowercased handle."""
    found: dict[str, Source] = {}
    try:
        raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
        rows = raw.get("sources", raw) if isinstance(raw, dict) else raw
        for row in rows:
            src = _from_row(row) if isinstance(row, dict) else None
            if src:
                found[src.handle.lower()] = src
    except (OSError, ValueError, TypeError):
        pass
    for src in SEEDS:
        found.setdefault(src.handle.lower(), src)
    return list(found.values())
