#!/usr/bin/env python3
"""Qdrant collection audit + cleanup for test_col_* artifacts.

T15 (G14 FIX) — Qdrant accumulates test_col_* collections from integration
tests that forget to clean up. This script audits and cleans them.

Usage:
    # Audit only (safe — no changes):
    python scripts/ops/qdrant_audit.py
    python scripts/ops/qdrant_audit.py --audit-only

    # Audit + cleanup (drops test_col_*):
    python scripts/ops/qdrant_audit.py --cleanup

    # Custom Qdrant URL:
    python scripts/ops/qdrant_audit.py --url http://qdrant.example:6333 --cleanup

    # CI gate mode (exit 1 if any test_col_* found):
    python scripts/ops/qdrant_audit.py --check

Cron suggestion (netcup):
    # Daily audit, notify if anything leaked:
    0 6 * * * python3 /root/scripts/qdrant_audit.py --audit-only | \\
        curl -s --data-binary @- http://localhost:9080/rmi-info || true
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def fetch_collections(base_url: str) -> list[dict]:
    with urllib.request.urlopen(f"{base_url}/collections", timeout=10) as r:
        return json.loads(r.read()).get("result", {}).get("collections", [])


def fetch_collection_info(base_url: str, name: str) -> dict:
    try:
        with urllib.request.urlopen(
            f"{base_url}/collections/{name}", timeout=5
        ) as r:
            return json.loads(r.read()).get("result", {})
    except Exception as e:
        return {"error": str(e)}


def delete_collection(base_url: str, name: str) -> bool:
    req = urllib.request.Request(
        f"{base_url}/collections/{name}", method="DELETE"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--url",
        default="http://localhost:6333",
        help="Qdrant base URL (default: http://localhost:6333)",
    )
    p.add_argument(
        "--audit-only",
        action="store_true",
        help="Only audit — never delete anything",
    )
    p.add_argument(
        "--cleanup",
        action="store_true",
        help="Audit + drop all test_col_* collections",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="CI mode: exit 1 if any test_col_* found (no output unless found)",
    )
    args = p.parse_args()

    cols = fetch_collections(args.url)
    test_like = [c["name"] for c in cols if c["name"].startswith("test_col")]
    production = [c["name"] for c in cols if not c["name"].startswith("test_col")]

    if args.check:
        if test_like:
            print(
                f"FAIL: {len(test_like)} test_col_* collection(s) found: "
                f"{test_like}",
                file=sys.stderr,
            )
            return 1
        return 0

    print(f"=== Qdrant Audit: {args.url} ===")
    print(f"Total collections: {len(cols)}")
    print(f"  Production: {len(production)}")
    print(f"  Test artifacts (test_col_*): {len(test_like)}")

    if not test_like:
        print("\nCLEAN — zero test_col_* artifacts.")
        return 0

    print("\n--- Test artifacts detail ---")
    for name in test_like:
        info = fetch_collection_info(args.url, name)
        points = info.get("points_count", "?")
        vectors_size = (
            info.get("config", {}).get("params", {}).get("vectors", {}).get("size", "?")
        )
        print(f"  {name}: {points} points, {vectors_size}-dim vectors")

    if args.audit_only:
        print(f"\n(Use --cleanup to drop these {len(test_like)} collection(s))")
        return 0

    if args.cleanup:
        print("\n--- Cleanup ---")
        dropped = 0
        for name in test_like:
            if delete_collection(args.url, name):
                print(f"  ✓ dropped {name}")
                dropped += 1
            else:
                print(f"  ✗ FAILED to drop {name}")
        print(f"\n{dropped}/{len(test_like)} dropped.")
        return 0 if dropped == len(test_like) else 1

    print("\n(Use --cleanup to drop these, or --audit-only to suppress this hint)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
