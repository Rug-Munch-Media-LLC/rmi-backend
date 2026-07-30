"""CLI entry points for MEV sandwich detector."""

from __future__ import annotations

import asyncio
import logging

from app.core.logging import get_logger

from .detector import MEVSandwichDetector

logger = get_logger(__name__)


async def _run_scan(args=None) -> None:
    detector = MEVSandwichDetector(chains=["ethereum", "bsc", "arbitrum", "base", "optimism", "polygon"])
    detector.register_pools_from_dict(
        [
            {
                "address": "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640",
                "chain": "ethereum",
                "dex_type": "uniswap_v2",
                "token0_symbol": "USDC",
                "token1_symbol": "WETH",
                "liquidity_usd": 500_000_000,
                "volume_24h_usd": 50_000_000,
                "fee_tier": 3000,
            },
            {
                "address": "0x5777d92f208679db4b9778590fa3cab3ac9e2168",
                "chain": "ethereum",
                "dex_type": "uniswap_v3",
                "token0_symbol": "DAI",
                "token1_symbol": "USDC",
                "liquidity_usd": 300_000_000,
                "volume_24h_usd": 10_000_000,
                "fee_tier": 100,
            },
        ]
    )
    logger.info("MEV & Sandwich Attack Detector - Scan Starting")
    logger.info(f"  Chains: {', '.join(detector.chains)}")
    report = await detector.scan()
    logger.info(report.summary())
    for s in report.top_sandwiches(limit=5):
        logger.info(f"  {s.summary()}")
    for b in report.top_bots(limit=5):
        logger.info(f"  {b.summary()}")
    for p in sorted(report.vulnerable_pools, key=lambda x: x["mev_vulnerability_score"], reverse=True)[:5]:
        logger.info(f"  {p['name']} ({p['chain']}) - score: {p['mev_vulnerability_score']} - {p['assessment']}")
    detector.save_bot_registry()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="MEV & Sandwich Attack Detector - real-time MEV detection")
    parser.add_argument("--pool", type=str, default=None, help="Analyze specific pool address")
    parser.add_argument(
        "--chain",
        type=str,
        default=None,
        choices=["ethereum", "bsc", "arbitrum", "base", "optimism", "polygon"],
        help="Limit scan to specific chain",
    )
    args = parser.parse_args()
    if args.chain:
        detector = MEVSandwichDetector(chains=[args.chain])
        asyncio.run(detector.scan())
    else:
        asyncio.run(_run_scan(args))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()
