"""Replay freshness metrics on a fixture: legacy vs unknown-aware."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "app" / "domains" / "news" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


resolve_published_at = _load("freshness").resolve_published_at
_w = _load("freshness_window")
freshness, FRESH, STALE, UNKNOWN = _w.freshness, _w.FRESH, _w.STALE, _w.UNKNOWN

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "news_entries_freshness.json"
INGESTED = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def main() -> int:
    entries = json.loads(FIXTURE.read_text())
    legacy = {"fresh": 0, "stale": 0, "unknown": 0}
    fixed = {"fresh": 0, "stale": 0, "unknown": 0}
    for e in entries:
        legacy[freshness(datetime(*e["published"][:6], tzinfo=UTC) if e.get("published") else INGESTED, INGESTED)] += 1
        fixed[freshness(resolve_published_at(e), INGESTED)] += 1
    n = len(entries)
    print(f"fixture n={n}")
    print(f"{'metric':24}{'legacy':>8}{'fixed':>8}")
    for k in ("fresh", "stale", "unknown"):
        print(f"{k:24}{legacy[k]:>8}{fixed[k]:>8}")
    print(f"{'fresh_share_pct':24}{100*legacy['fresh']//n:>8}{100*fixed['fresh']//n:>8}")
    print(f"{'unknown_excluded':24}{'no':>8}{'yes':>8}")
    assert fixed[UNKNOWN] == 4
    assert fixed[FRESH] == 4
    assert fixed[STALE] == 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
