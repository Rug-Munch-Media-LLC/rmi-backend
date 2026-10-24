#!/usr/bin/env python3
"""methodology_fleet.py — per-repo methodology baseline for the whole public set.

The fleet baseline records the CURRENT finding count per public repo. Any
increase in any repo fails; raising it requires an explicit reason file, which
is recorded in the baseline (same discipline as the antipattern ratchet).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from methodology_patterns import counts_of, scan_repo
from methodology_policy import classification

FLEET_BASELINE = Path("scripts/methodology-fleet-baseline.json")


@dataclass(frozen=True)
class FleetGate:
    """The inputs every fleet pass needs, bundled so no call takes six args."""

    root: Path
    policy: dict
    patterns: dict
    repos_root: Path


def _scan_public(repos_root: Path, public: set[str], patterns: dict, old: dict) -> tuple[dict, bool]:
    """Scan every checked-out public repo; return per-repo totals and any growth."""
    current: dict[str, dict] = {}
    failed = False
    for repo in sorted(public):
        rroot = repos_root / repo
        if not (rroot / ".git").exists():
            print(f"  skip {repo} (not checked out)")
            continue
        counts = counts_of(scan_repo(rroot, patterns))
        total = sum(counts.values())
        current[repo] = {"total": total, "rules": counts}
        prev = old.get(repo, {}).get("total", 0)
        delta = total - prev
        failed = failed or delta > 0
        print(f"  {'!!' if delta > 0 else '  '} {repo:<22} {total:>4} (baseline {prev}, {delta:+d})")
    return current, failed


def _rewrite_base(path: Path, base: dict, current: dict, old: dict, reason: str | None) -> int:
    """Persist the new fleet baseline, refusing growth without a reason file."""
    grown = {
        r: (old.get(r, {}).get("total", 0), v["total"])
        for r, v in current.items()
        if v["total"] > old.get(r, {}).get("total", 0)
    }
    if grown and not reason:
        print("REFUSING to raise the fleet baseline without --reason <file>:")
        for r, (before, after) in grown.items():
            print(f"  {r}: {before} -> {after}")
        return 1
    base["repos"] = current
    base.setdefault("reasons", []).append({"date": date.today().isoformat(), "reason": reason or "baseline lowered"})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base, indent=1) + "\n")
    print(f"fleet baseline updated: {path}")
    return 0


def gate_fleet(gate: FleetGate, update: bool, reason: str | None) -> int:
    """Scan every public repo and ratchet the per-repo totals."""
    public, _ = classification(gate.policy)
    path = gate.root / FLEET_BASELINE
    base = json.loads(path.read_text()) if path.is_file() else {"repos": {}}
    old = base.get("repos", {})
    current, failed = _scan_public(gate.repos_root, public, gate.patterns, old)
    if update:
        return _rewrite_base(path, base, current, old, reason)
    return 1 if failed else 0
