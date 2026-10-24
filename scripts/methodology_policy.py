#!/usr/bin/env python3
"""methodology_policy.py — public/private classification for the methodology gate.

Public/private is decided by OPEN-CORE-POLICY.json, never by a local allowlist.
A repo-local snapshot (scripts/methodology-policy.json) lets CI decide without a
checkout of the private standards repo. No policy found is FATAL: the gate never
assumes a repo is private in order to skip itself.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path


def find_policy(root: Path, explicit: str | None = None) -> dict:
    """Locate the open-core policy: explicit → repo snapshot → sibling → canonical."""
    # No absolute fleet path in this file: it is published in public repos, and
    # naming the fleet layout is what the leak gate blocks. The sibling checkout
    # resolves on the dev box, METHODOLOGY_POLICY covers a worktree, and the
    # committed snapshot covers CI.
    candidates = [
        explicit,
        os.environ.get("METHODOLOGY_POLICY"),
        root / "scripts" / "methodology-policy.json",
        root.parent / "standards" / "OPEN-CORE-POLICY.json",
    ]
    for cand in candidates:
        if cand and Path(cand).is_file():
            return json.loads(Path(cand).read_text())
    raise SystemExit("methodology-gate: FAIL CLOSED — OPEN-CORE-POLICY.json not found")


def classification(policy: dict) -> tuple[set[str], set[str]]:
    """Return (public, private) repo-name sets, alias-resolved."""
    # Canonical policy uses public_oss; the committed CI snapshot uses
    # public_repos. Both are the same list, and both must be accepted.
    public = set(policy.get("public_oss") or policy.get("public_repos") or [])
    private = set(policy.get("private_proprietary", []))
    for alias, target in (policy.get("aliases") or {}).items():
        (public if target in public else private).add(alias)
    return public, private


def sync_policy(root: Path, policy: dict) -> int:
    """Snapshot the public set into the repo so CI needs no private checkout."""
    public, _ = classification(policy)
    out = {
        "generated_from": "standards/OPEN-CORE-POLICY.json",
        "generated": date.today().isoformat(),
        "public_repos": sorted(public),
        "aliases": policy.get("aliases") or {},
    }
    dest = root / "scripts/methodology-policy.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1) + "\n")
    print(f"policy snapshot written: {dest} ({len(public)} public repos)")
    return 0
