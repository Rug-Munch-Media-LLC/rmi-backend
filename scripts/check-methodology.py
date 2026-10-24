#!/usr/bin/env python3
"""check-methodology.py — fail the build on published METHOD, allow published CODE.

Enforces "Code, Not Methods" (standards/METHODOLOGY-PROTECTION.md): scoring
weight tables, magic thresholds, signal lists, judgement prompts and imported
scoring constants are METHOD, and METHOD does not ship in a public repo.

Repo-aware: public/private comes from OPEN-CORE-POLICY.json, so a private repo
exits 0 with a notice. Path-aware: tests, fixtures and examples are skipped by
data in methodology-patterns.json. Fail closed: missing pattern data is fatal.

Ratchet: counts may only go DOWN; raising the baseline needs --reason <file>,
and that reason is written into the baseline.

  check-methodology.py --repo .                      # gate (blocking)
  check-methodology.py --repo . --update-baseline --reason notes/why.md
  REPOS_ROOT=<repos dir> check-methodology.py --fleet  # all public repos
  check-methodology.py --sync-policy                 # snapshot the public set
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from methodology_fleet import gate_fleet
from methodology_patterns import counts_of, load_patterns, scan_repo
from methodology_policy import classification, find_policy, sync_policy

ADVICE = (
    "move the method behind the seam (keep it in a private package or service and\n"
    "     call it over an interface), then publish an example that shows the shape\n"
    "     without the weights, thresholds or signal vocabulary."
)


def reason_text(path: str | None) -> str:
    """Read a reason file, dropping comment lines; return one normalised line."""
    if not path:
        return ""
    lines = [ln for ln in Path(path).read_text().splitlines() if not ln.lstrip().startswith("#")]
    return " ".join(" ".join(lines).split())


def report(findings: list[dict], counts: dict[str, int], baseline: dict[str, int], name: str) -> int:
    """Print the ratchet table plus offending locations. Returns an exit code."""
    failed = [r for r, n in counts.items() if n > baseline.get(r, 0)]
    print(f"methodology-gate [{name}]: {sum(counts.values())} finding(s) across {len(counts)} rule(s)")
    for rule in sorted(counts, key=lambda r: -counts[r]):
        print(f"  {'!!' if rule in failed else '  '} {rule:<19} {counts[rule]:>4}  (baseline {baseline.get(rule, 0)})")
    if not failed:
        print("  ratchet holds — no increase")
        return 0
    print()
    for f in findings:
        if f["rule"] in failed:
            print(f"  {f['file']}:{f['line']}  rule={f['rule']}  {f['detail']}")
    print("\n🚫 BLOCKED: published method detected in a PUBLIC repo.")
    print("   What to do instead: " + ADVICE)
    print("   Ratchet: fix it or move it. Raising the baseline requires")
    print("   --reason <file>; the reason is recorded in the baseline.")
    return 1


def gate_repo(
    root: Path,
    policy: dict,
    patterns: dict,
    baseline_path: Path,
    update: bool,
    reason: str | None,
) -> int:
    """Gate one repo: public check, scan, ratchet compare or deliberate update."""
    public, _private = classification(policy)
    name = root.name
    if name not in public:
        print(f"methodology-gate [{name}]: not in public_oss — skipped (private work is not gated)")
        return 0
    findings = scan_repo(root, patterns)
    counts = counts_of(findings)
    base = json.loads(baseline_path.read_text()) if baseline_path.is_file() else {"rules": {}, "total": 0}
    old = base.get("rules", {})
    if not update:
        return report(findings, counts, old, name)
    growth = {r: (old.get(r, 0), n) for r, n in counts.items() if n > old.get(r, 0)}
    if growth and not reason:
        print("REFUSING to raise the baseline (shrink-only) without --reason <file>:")
        for rule, (before, after) in growth.items():
            print(f"  {rule}: {before} -> {after}")
        return 1
    base.setdefault("reasons", []).append(
        {
            "date": date.today().isoformat(),
            "rules": {r: list(v) for r, v in growth.items()},
            "reason": reason or "baseline lowered",
        }
    )
    base.update({"rules": counts, "total": sum(counts.values())})
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(base, indent=1) + "\n")
    print(f"baseline updated: {baseline_path} (total {base['total']})")
    return 0


def main() -> int:
    """CLI entry."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--repos-root", default=os.environ.get("REPOS_ROOT", ".."))
    ap.add_argument("--patterns")
    ap.add_argument("--policy")
    ap.add_argument("--baseline")
    ap.add_argument("--fleet", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    ap.add_argument("--sync-policy", action="store_true")
    ap.add_argument("--reason")
    args = ap.parse_args()
    root = Path(args.repo).resolve()
    try:
        patterns = load_patterns(args.patterns)
    except (OSError, ValueError) as exc:
        print(f"methodology-gate: FAIL CLOSED — {exc}", file=sys.stderr)
        return 2
    policy = find_policy(root, args.policy)
    if args.sync_policy:
        return sync_policy(root, policy)
    reason = reason_text(args.reason)
    if reason and len(reason) < 40:
        print(
            "methodology-gate: --reason too short; explain which method is being published",
            file=sys.stderr,
        )
        return 2
    if args.fleet:
        return gate_fleet(root, policy, patterns, Path(args.repos_root), args.update_baseline, reason)
    baseline = Path(args.baseline) if args.baseline else root / "scripts/methodology-baseline.json"
    return gate_repo(root, policy, patterns, baseline, args.update_baseline, reason)


if __name__ == "__main__":
    sys.exit(main())
