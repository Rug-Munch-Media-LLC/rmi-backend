"""
Oracle Manipulation Detector — CLI Entry Point
================================================
Command-line interface for running scans.
"""

import argparse
import asyncio
import json
import logging
import re
import time

from app.core.logging import get_logger

from .constants import SUPPORTED_CHAINS
from .detector import OracleManipulationDetector

logger = get_logger(__name__)


def main() -> None:
    """Main CLI entry point for the Oracle Manipulation Detector."""
    parser = argparse.ArgumentParser(
        description="Oracle Manipulation Detector - detect price oracle attacks across EVM chains"
    )
    parser.add_argument("--pool", type=str, help="Analyze a specific pool address")
    parser.add_argument("--tx", type=str, help="Analyze a specific transaction hash")
    parser.add_argument(
        "--blocks", type=int, default=50, help="Number of blocks to scan (default: 50)"
    )
    parser.add_argument(
        "--chain", type=str, default="ethereum", help="Chain to scan (default: ethereum)"
    )
    parser.add_argument("--chains", type=str, help="Comma-separated list of chains to scan")
    parser.add_argument("--monitor", action="store_true", help="Continuous monitoring mode")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--pair", type=str, default="", help="Pair name for pool analysis (e.g., WETH/USDC)"
    )
    parser.add_argument("--check-feeds", action="store_true", help="Check Chainlink feeds only")
    args = parser.parse_args()

    # ── Input Validation ──
    if args.pool and not re.match(r"^0x[a-fA-F0-9]{40}$", args.pool):
        parser.error(f"Invalid pool address format: {args.pool}")
    if args.tx and not re.match(r"^0x[a-fA-F0-9]{64}$", args.tx):
        parser.error(f"Invalid transaction hash format: {args.tx}")
    if args.blocks is not None and (args.blocks < 1 or args.blocks > 10000):
        parser.error(f"Blocks must be 1-10000, got: {args.blocks}")
    if args.chain and args.chain not in SUPPORTED_CHAINS and (not args.chains):
        logger.warning("Unknown chain '%s' - proceeding anyway", args.chain)
    if args.chains:
        for c in [c.strip() for c in args.chains.split(",")]:
            if c not in SUPPORTED_CHAINS:
                logger.warning("Unknown chain '%s' in --chains - proceeding anyway", c)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    chains = [args.chain]
    if args.chains:
        chains = [c.strip() for c in args.chains.split(",")]

    detector = OracleManipulationDetector(chain=args.chain)

    if args.pool:
        report = asyncio.run(detector.analyze_pool(args.pool, args.pair))
    elif args.tx:
        report = asyncio.run(detector.analyze_transaction(args.tx))
    elif args.check_feeds:
        reads = asyncio.run(detector.check_chainlink_feeds())
        if args.json:
            print(json.dumps([r.to_dict() for r in reads], indent=2))
        else:
            for read in reads:
                status = "STALE" if read.is_stale() else "FRESH"
                print(
                    f"[{status}] {read.oracle_address} - price={read.reported_price} age={read.price_age_seconds:.0f}s"
                )
    else:
        report = asyncio.run(detector.scan(blocks_back=args.blocks, chains=chains))
        if args.monitor:
            print(f"Monitoring {args.chain} - Ctrl+C to stop")
            try:
                while True:
                    report = asyncio.run(detector.scan(blocks_back=args.blocks, chains=chains))
                    if report.total_incidents > 0:
                        for inc in report.incidents:
                            print(inc.summary())
                            print("---")
                    time.sleep(30)
            except KeyboardInterrupt:
                print("\nMonitoring stopped.")
        elif args.json:
            print(report.json())
        else:
            print(report.to_dict())


if __name__ == "__main__":
    main()
