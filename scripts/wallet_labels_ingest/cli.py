#!/usr/bin/env python3
"""CLI wiring for the wallet-label ingestion pipeline.

Primary function: ``main``. Source dispatch, per-source argument shapes, and
the final summary all live here; each source itself lives in its own module.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .clients import get_ch_client, get_redis_client
from .logging_setup import logger
from .report import show_report
from .src_bluepages_pair import ingest_bluepages, ingest_walletlabels_xyz
from .src_builtin import ingest_builtin_contracts
from .src_chainabuse import ingest_chainabuse
from .src_cryptoscamdb import ingest_cryptoscamdb
from .src_etherscan import ingest_etherscan_labels
from .src_github_community import ingest_github_community
from .src_metamask import ingest_metamask_phishing
from .src_open_labels import ingest_open_labels
from .src_rolodeth import ingest_rolodeth
from .src_scanner_findings import ingest_scanner_findings

__all__ = ["SOURCE_REGISTRY", "main"]

SourceFn = Callable[..., Awaitable[dict[str, int]]]

SOURCE_REGISTRY: dict[str, SourceFn] = {
    "builtin_contracts": ingest_builtin_contracts,
    "etherscan_labels": ingest_etherscan_labels,
    "cryptoscamdb": ingest_cryptoscamdb,
    "chainabuse": ingest_chainabuse,
    "rolodeth": ingest_rolodeth,
    "walletlabels_xyz": ingest_walletlabels_xyz,
    "bluepages": ingest_bluepages,
    "metamask_phishing": ingest_metamask_phishing,
    "open_labels": ingest_open_labels,
    "scanner_findings": ingest_scanner_findings,
    "github_community": ingest_github_community,
}

SCAM_SOURCE_NAMES = (
    "builtin_contracts",
    "etherscan_labels",
    "cryptoscamdb",
    "chainabuse",
    "metamask_phishing",
    "scanner_findings",
    "github_community",
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser (flags identical to the original script)."""
    parser = argparse.ArgumentParser(
        description="Ingest wallet labels from public directories into ClickHouse + Redis",
    )
    parser.add_argument("--all", action="store_true", help="Ingest ALL sources")
    parser.add_argument("--scam-sources", action="store_true", help="Ingest all scam/intelligence sources")
    parser.add_argument("--source", type=str, default="", help="Specific source name")
    parser.add_argument("--chain", type=str, default="all", help="Chain filter (ethereum, solana, bsc, etc.)")
    parser.add_argument("--report", action="store_true", help="Show label inventory report")
    parser.add_argument("--quiet", action="store_true", help="Reduce logging output")
    return parser


def select_sources(parser: argparse.ArgumentParser, args: argparse.Namespace) -> list[tuple[str, SourceFn]]:
    """Resolve the CLI flags into the ordered (name, fn) pairs to run."""
    if args.all:
        return list(SOURCE_REGISTRY.items())
    if args.scam_sources:
        return [(name, SOURCE_REGISTRY[name]) for name in SCAM_SOURCE_NAMES]
    if args.source:
        source_name = args.source.lower()
        if source_name not in SOURCE_REGISTRY:
            logger.error(f"Unknown source: {source_name}")
            logger.error(f"Available sources: {', '.join(sorted(SOURCE_REGISTRY.keys()))}")
            sys.exit(1)
        return [(source_name, SOURCE_REGISTRY[source_name])]
    parser.print_help()
    return []


async def _run_source(source_name: str, source_fn: SourceFn, ch: Any, redis: Any | None, chain: str) -> dict[str, int]:
    """Invoke one source with the argument shape it expects."""
    if source_name in ("etherscan_labels",):
        return await source_fn(ch, redis, chain=chain)
    if source_name in ("walletlabels_xyz", "bluepages"):
        return await source_fn(ch, redis, chain=chain if chain != "all" else "ethereum")
    return await source_fn(ch, redis)


async def run_sources(sources: list[tuple[str, SourceFn]], ch: Any, redis: Any | None, chain: str) -> dict[str, int | float]:
    """Run every source, accumulating totals; one failing source never aborts."""
    grand_totals: dict[str, int | float] = {"wallet_labels": 0, "scam_addresses": 0}
    start = time.time()
    for source_name, source_fn in sources:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"RUNNING: {source_name}")
        logger.info(f"{'=' * 60}")
        try:
            result = await _run_source(source_name, source_fn, ch, redis, chain)
            for key in grand_totals:
                grand_totals[key] += result.get(key, 0)
            logger.info(
                f"  [{source_name}] {result.get('wallet_labels', 0):,} labels, "
                f"{result.get('scam_addresses', 0):,} scam addresses "
                f"(total elapsed: {time.time() - start:.1f}s)"
            )
        except Exception as e:
            logger.error(f"Source {source_name} failed: {e}", exc_info=True)
    grand_totals["elapsed"] = time.time() - start
    return grand_totals


async def main() -> None:
    """CLI entry: connect, dispatch sources, print the summary + report."""
    parser = build_parser()
    args = parser.parse_args()

    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    ch = get_ch_client()
    redis = get_redis_client()
    if not ch:
        logger.error("ClickHouse connection required. Set CLICKHOUSE_HOST/CLICKHOUSE_PORT env vars.")
        sys.exit(1)
    if not redis:
        logger.warning("Redis unavailable - labels will be stored in ClickHouse only")

    if args.report:
        await show_report(ch)
        return

    sources = select_sources(parser, args)
    if not sources:
        return

    grand_totals = await run_sources(sources, ch, redis, args.chain)

    logger.info(f"\n{'=' * 60}")
    logger.info("INGESTION COMPLETE")
    logger.info(f"{'=' * 60}")
    logger.info(f"Total wallet_labels added: {grand_totals['wallet_labels']:,}")
    logger.info(f"Total scam_addresses added: {grand_totals['scam_addresses']:,}")
    logger.info(f"Total elapsed: {grand_totals['elapsed']:.1f}s")

    await show_report(ch)
