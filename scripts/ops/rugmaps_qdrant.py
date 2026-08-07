#!/usr/bin/env python3
"""RugMaps Qdrant ops — create collections, audit, health metrics.

Phase 0 ops companion to ``app.domains.rugmaps.qdrant``. Creates the
``rgm_*`` collections on the Qdrant instance, audits their config, and
reports health metrics (point counts, dimension drift).

Usage:
    # Create missing rgm_* collections (idempotent):
    python scripts/ops/rugmaps_qdrant.py --create

    # Audit current rgm_* state (exists, dim, distance, points):
    python scripts/ops/rugmaps_qdrant.py --audit

    # Health metrics (JSON, for cron/alerting):
    python scripts/ops/rugmaps_qdrant.py --health --json

    # Custom Qdrant URL:
    python scripts/ops/rugmaps_qdrant.py --create --url http://qdrant.example:6333

    # CI gate mode: exit 1 unless ALL rgm_* collections exist with
    # the expected dimension:
    python scripts/ops/rugmaps_qdrant.py --check

Cron suggestion:
    # Daily health check, alert on failure:
    30 4 * * * python3 /app/scripts/ops/rugmaps_qdrant.py --health --json \\
        | curl -s --data-binary @- http://localhost:9080/rmi-info || true
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.logging import get_logger
from app.domains.rugmaps.qdrant import COLLECTIONS, RugMapsQdrant

logger = get_logger(__name__)


async def _run(args: argparse.Namespace) -> int:
    qdrant = RugMapsQdrant(url=args.url)
    try:
        if args.create:
            created = await qdrant.create_collections()
            new = [name for name, ok in created.items() if ok]
            existing = [name for name, ok in created.items() if not ok]
            print(f"=== RugMaps Qdrant create ({args.url}) ===")
            print(f"Created: {len(new)}  Existing/failed: {len(existing)}")
            for name in new:
                print(f"  ✓ created {name} ({COLLECTIONS[name]}-dim dense, cosine)")
            for name in existing:
                print(f"  - {name} already present or failed")
            if len(new) == 0 and len(existing) == len(COLLECTIONS):
                print("\nAll rgm_* collections already exist.")
            return 0

        if args.audit or args.check:
            rows = await qdrant.audit()
            if args.json:
                print(json.dumps({"audit": rows}, indent=2, default=str))
            else:
                print(f"=== RugMaps Qdrant audit ({args.url}) ===")
                ok = True
                for row in rows:
                    if row.get("error"):
                        print(f"  ✗ {row['collection']}: Qdrant error ({row['error']})")
                        ok = False
                        continue
                    if not row["exists"]:
                        print(f"  ✗ {row['collection']}: MISSING")
                        ok = False
                        continue
                    mismatch = row.get("mismatch", False)
                    marker = "✗" if mismatch else "✓"
                    if mismatch:
                        ok = False
                    print(
                        f"  {marker} {row['collection']}: "
                        f"{row['points_count']} pts, {row['dim']}-dim {row['distance']} "
                        f"(expected {row['expected_dim']}-dim)"
                    )
                if args.check:
                    return 0 if ok else 1
            return 0

        if args.health:
            metrics = await qdrant.health()
            if args.json:
                print(json.dumps(metrics, indent=2, default=str))
            else:
                print(f"=== RugMaps Qdrant health ({args.url}) ===")
                print(f"Status: {metrics.get('status')}")
                print(f"Collections: {metrics.get('collections')}")
                print(f"Total points: {metrics.get('total_points')}")
                for col in metrics.get("collections_status", []):
                    if col.get("error"):
                        state = f"✗ ERROR ({col['error']})"
                    elif col["exists"]:
                        state = "✓"
                    else:
                        state = "✗ MISSING"
                    print(f"  {state} {col['collection']}: {col['points_count']} pts")
            return 0 if metrics.get("status") == "ok" else 1

        print("No action requested. Use --create, --audit, --health, or --check.", file=sys.stderr)
        return 2
    finally:
        await qdrant.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--url",
        default=None,
        help="Qdrant base URL (default: QDRANT_URL env or http://rmi-qdrant:6333)",
    )
    p.add_argument("--create", action="store_true", help="Create missing rgm_* collections")
    p.add_argument("--audit", action="store_true", help="Audit rgm_* collection state")
    p.add_argument("--health", action="store_true", help="Report health metrics")
    p.add_argument("--check", action="store_true", help="CI mode: exit 1 if any rgm_* collection is missing/mismatched")
    p.add_argument("--json", action="store_true", help="Emit JSON output (audit/health)")
    args = p.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
