"""CLI entry point for the honeypot detector."""
import asyncio, logging
from app.core.logging import get_logger
from .constants import EVM_CHAINS
from .detector import HoneypotDetector
logger = get_logger(__name__)

async def main_async() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Smart Contract Honeypot & Malicious Token Detector")
    parser.add_argument("address", type=str, help="Token contract address (0x...)")
    parser.add_argument("--chain", type=str, default="ethereum", choices=list(EVM_CHAINS.keys()), help="Blockchain (default: ethereum)")
    parser.add_argument("--deep-scan", action="store_true", help="Enable deeper on-chain analysis (slower)")
    parser.add_argument("--format", type=str, choices=["text", "json"], default="text", help="Output format (default: text)")
    args = parser.parse_args()
    detector = HoneypotDetector()
    result = await detector.analyze_token(address=args.address, chain=args.chain, deep_scan=args.deep_scan)
    if args.format == "json": print(result.report(fmt="json"))
    else: print(result.report())

def main() -> None:
    asyncio.run(main_async())
