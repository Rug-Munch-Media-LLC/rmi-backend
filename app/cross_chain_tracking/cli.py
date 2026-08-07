#!/usr/bin/env python3
"""
Cross-Chain Entity Tracker — CLI entry point.
===============================================
Run batch processing to find multi-chain deployers and create Neo4j edges.

Usage:
    python -m app.cross_chain_tracking.cli [--min-chains 2] [--limit 500] [--dry-run]
    python -m app.cross_chain_tracking.cli --chains ethereum,bsc,polygon --min-chains 2
    python -m app.cross_chain_tracking.cli --stats
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.logging import get_logger

logger = get_logger("cross_chain_tracking.cli")


async def cmd_run(args: argparse.Namespace) -> int:
    """Run the batch pipeline: find multi-chain deployers and persist."""
    from .entity_tracker import get_entity_tracker

    chain_filter = args.chains.split(",") if args.chains else None
    tracker = get_entity_tracker()

    logger.info(
        "Starting cross-chain batch pipeline (min_chains=%d, limit=%d, dry_run=%s)",
        args.min_chains,
        args.limit,
        args.dry_run,
    )

    result = await tracker.run_batch_pipeline(
        min_chains=args.min_chains,
        chain_filter=chain_filter,
        limit=args.limit,
        dry_run=args.dry_run,
    )

    duration = result.get("duration_ms", 0)
    found = result.get("multi_chain_deployers_found", 0)
    created = result.get("entities_created", 0)
    edges = result.get("edges_created", 0)
    links = result.get("wallet_links_created", 0)
    errors = result.get("errors", [])

    print(f"\n{'=' * 60}")
    print("Cross-Chain Batch Pipeline Results")
    print(f"{'=' * 60}")
    print(f"  Duration:           {duration:.1f}ms")
    print(f"  Multi-chain found:  {found}")
    print(f"  Entities created:   {created}")
    print(f"  Edges created:      {edges}")
    print(f"  Wallet links saved: {links}")
    if errors:
        print(f"  Errors:             {len(errors)}")
        for err in errors[:5]:
            print(f"    - {err}")
    if args.dry_run:
        print("\n  DRY RUN — no changes persisted")
    print(f"{'=' * 60}\n")

    return 0 if not errors else 1


async def cmd_stats(args: argparse.Namespace) -> int:
    """Show statistics about cross-chain data."""
    from .clickhouse_reader import fetch_chains_with_data, fetch_deployment_counts_by_chain

    chains = await fetch_chains_with_data()
    counts = await fetch_deployment_counts_by_chain(limit=30)

    print(f"\n{'=' * 60}")
    print("Cross-Chain Statistics")
    print(f"{'=' * 60}")

    print(f"\n  Chains with deployer data: {len(chains)}")
    for c in sorted(chains):
        print(f"    - {c}")

    print("\n  Deployment counts by chain:")
    for row in counts:
        print(f"    {row['chain']:20s}  {row['deployments']:>8,} deployments  {row['unique_deployers']:>8,} deployers")

    # Count multi-chain deployers
    from .deployer_matcher import DeployerMatcher

    matcher = DeployerMatcher()
    multi_chain = await matcher.find_multi_chain_deployers(min_chains=2, limit=10_000)
    print(f"\n  Multi-chain deployers (2+ chains): {len(multi_chain)}")

    # Count per chain-count bucket
    buckets: dict[int, int] = {}
    for mcd in multi_chain:
        buckets[mcd.chain_count] = buckets.get(mcd.chain_count, 0) + 1
    for k in sorted(buckets):
        print(f"    {k} chains: {buckets[k]:>8,} deployers")

    print(f"{'=' * 60}\n")
    return 0


async def cmd_resolve(args: argparse.Namespace) -> int:
    """Resolve a single address to its cross-chain profile."""
    from .entity_tracker import get_entity_tracker

    tracker = get_entity_tracker()
    profile = await tracker.resolve(args.address, args.chain)

    print(f"\n{'=' * 60}")
    print("Cross-Chain Profile")
    print(f"{'=' * 60}")
    print(f"  Entity ID:      {profile.entity_id}")
    print(f"  Primary:        {profile.primary_address[:20]}... on {profile.primary_chain}")
    print(f"  Chains:         {len(profile.chains)}")
    print(f"  Total addrs:    {profile.total_addresses}")
    print(f"  Deployments:    {profile.total_deployments}")
    print(f"  Risk Score:     {profile.risk_score}")

    print("\n  Addresses:")
    for addr in profile.addresses:
        print(f"    {addr.address[:24]:24s}  {addr.chain:12s}  {addr.confidence:.0%}  {addr.method}")
    print(f"{'=' * 60}\n")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-Chain Entity Tracker",
    )
    parser.set_defaults(func=cmd_stats)

    sub = parser.add_subparsers(title="commands", dest="command")

    # run
    run_p = sub.add_parser("run", help="Run batch pipeline (find + persist multi-chain deployers)")
    run_p.add_argument("--min-chains", type=int, default=2, help="Minimum chains (default: 2)")
    run_p.add_argument("--limit", type=int, default=500, help="Max deployers (default: 500)")
    run_p.add_argument("--chains", type=str, default=None, help="Comma-separated chain filter")
    run_p.add_argument("--dry-run", action="store_true", help="Dry run (no persistence)")
    run_p.set_defaults(func=cmd_run)

    # stats
    stats_p = sub.add_parser("stats", help="Show cross-chain statistics")
    stats_p.set_defaults(func=cmd_stats)

    # resolve
    res_p = sub.add_parser("resolve", help="Resolve a single address's cross-chain profile")
    res_p.add_argument("address", help="Wallet address to resolve")
    res_p.add_argument("--chain", default="ethereum", help="Chain (default: ethereum)")
    res_p.set_defaults(func=cmd_resolve)

    args = parser.parse_args()
    if hasattr(args, "func"):
        sys.exit(asyncio.run(args.func(args)))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
