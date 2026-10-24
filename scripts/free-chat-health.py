#!/usr/bin/env python3
"""Probe the free chat pool and prove fail-closed selection.

Usage:
  free-chat-health.py                # live matrix, exit 0 when >=1 lane is healthy
  free-chat-health.py --json         # machine-readable matrix
  free-chat-health.py --failover-demo  # force a 429 on lane 1 and show the re-pick

Exit codes: 0 = pool has a healthy lane, 2 = free_pool_exhausted, 1 = spec error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.assistant_gateway.free_pool import (  # noqa: E402
    FreePoolExhausted,
    PoolState,
    load_pool,
    pick_model,
    probe_pool,
)
from app.assistant_gateway.free_pool.spec import PoolSpecError  # noqa: E402


def _failover_demo(report: dict, pool_state: PoolState) -> dict:
    """Force the first lane to fail twice and show the selection that follows."""
    rows = report["results"]
    if not rows:
        return {"error": "no providers to demonstrate"}
    first = rows[0]
    for _ in range(2):
        pool_state.record(first["provider"], ok=False, error="forced 429 (demo)")
    try:
        chosen = pick_model(load_pool(), pool_state)
        return {
            "forced_failure": first["provider"],
            "forced_error": "429 rate_limited (injected)",
            "re_picked": chosen.id,
            "re_picked_model": chosen.model,
            "breaker_state": pool_state.snapshot()[first["provider"]]["breaker"],
        }
    except FreePoolExhausted as exc:
        return {"forced_failure": first["provider"], "error": exc.as_error()}


def _print_matrix(report: dict) -> None:
    print(f"pool={report['pool']} probed={report['probed']} healthy={len(report['healthy'])}")
    print(f"{'provider':26} {'status':>6} {'secs':>6}  model")
    for row in report["results"]:
        status = row["status"] if row["status"] is not None else "ERR"
        print(f"{row['provider']:26} {status!s:>6} {row['seconds']!s:>6}  {row['model']}")
        if row["reason"]:
            print(f"{'':26} reason: {row['reason']}")


async def _main(args: argparse.Namespace) -> int:
    try:
        spec = load_pool()
    except PoolSpecError as exc:
        print(f"[BLOCKED] pool spec: {exc}", file=sys.stderr)
        return 1
    report = await probe_pool(spec, secrets_dir=args.secrets_dir)
    pool_state = PoolState(spec)
    for row in report["results"]:
        pool_state.record(row["provider"], ok=row["ok"], error=row["reason"])

    if args.failover_demo:
        report["failover"] = _failover_demo(report, pool_state)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_matrix(report)
        if "failover" in report:
            print("failover:", json.dumps(report["failover"]))

    if not report["healthy"]:
        print(f"[EXHAUSTED] {spec.fail_closed_code}: no free lane answered", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Free chat pool health probe")
    parser.add_argument("--json", action="store_true", help="emit the raw matrix")
    parser.add_argument("--failover-demo", action="store_true", help="inject a 429 on lane 1")
    parser.add_argument(
        "--secrets-dir",
        default=None,
        help="directory holding provider key files (default: $FREE_POOL_SECRETS_DIR)",
    )
    raise SystemExit(asyncio.run(_main(parser.parse_args())))
