"""
Liquidation Cascade — CLI Entry Point
=======================================
"""

from __future__ import annotations

import asyncio

from app.core.logging import get_logger
from app.domains.analysis.liquidation.analyzer import LiquidationCascadeAnalyzer
from app.domains.analysis.liquidation.fetcher import SUPPORTED_CHAINS

logger = get_logger(__name__)


async def main_async() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Cross-Chain Liquidation Cascade Risk Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("wallet", help="Wallet address (EVM 0x... or Solana)")
    parser.add_argument(
        "--chains",
        default=",".join(list(SUPPORTED_CHAINS.keys())[:4]),
        help="Comma-separated chains (default: ethereum,base,arbitrum,optimism)",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--no-onchain", action="store_true", help="Skip on-chain data fetching")

    args = parser.parse_args()
    chains = [c.strip() for c in args.chains.split(",") if c.strip()]
    analyzer = LiquidationCascadeAnalyzer()
    result = await analyzer.analyze(
        wallet=args.wallet,
        chains=chains,
        use_onchain=not args.no_onchain,
    )

    if args.format == "json":
        logger.info(result.report(format="json"))
    else:
        logger.info(result.report())


def main() -> None:
    """CLI entry point (synchronous wrapper)."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
