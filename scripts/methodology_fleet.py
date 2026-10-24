#!/usr/bin/env python3
"""methodology_fleet.py — per-repo methodology baseline for the whole public set.

The fleet baseline records the CURRENT finding count per public repo. Any
increase in any repo fails; raising it requires an explicit reason file, which
is recorded in the baseline (same discipline as the antipattern ratchet).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from methodology_patterns import counts_of, scan_repo
from methodology_policy import classification

FLEET_BASELINE = Path("scripts/methodology-fleet-baseline.json")


def gate_fleet(
    root: Path,
    policy: dict,
    patterns: dict,
    repos_root: Path,
    update: bool,
    reason: str | None,
) -> int:
    """Scan every public repo and ratchet the per-repo totals."""
    public, _ = classification(policy)
    path = root / FLEET_BASELINE
    base = json.loads(path.read_text()) if path.is_file() else {"repos": {}}
    old = base.get("repos", {})
    current: dict[str, dict] = {}
    failed = False
    for repo in sorted(public):
        rroot = repos_root / repo
        if not (rroot / ".git").exists():
            print(f"  skip {repo} (not checked out)")
            continue
        counts = counts_of(scan_repo(rroot, patterns))
        current[repo] = {"total": sum(counts.values()), "rules": counts}
        prev = old.get(repo, {}).get("total", 0)
        delta = current[repo]["total"] - prev
        failed = failed or delta > 0
        print(f"  {'!!' if delta > 0 else '  '} {repo:<22} {current[repo]['total']:>4} (baseline {prev}, {delta:+d})")
    if update:
        grown = {
            r: (old.get(r, {}).get("total", 0), v["total"])
            for r, v in current.items()
            if v["total"] > old.get(r, {}).get("total", 0)
        }
        if grown and not reason:
            print("REFUSING to raise the fleet baseline without --reason <file>:")
            for r, (o, n) in grown.items():
                print(f"  {r}: {o} -> {n}")
            return 1
        base["repos"] = current
        base.setdefault("reasons", []).append(
            {"date": date.today().isoformat(), "reason": reason or "baseline lowered"}
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(base, indent=1) + "\n")
        print(f"fleet baseline updated: {path}")
        return 0
    return 1 if failed else 0
