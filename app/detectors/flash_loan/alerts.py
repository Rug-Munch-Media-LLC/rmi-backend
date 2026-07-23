"""
Alert Generation for Flash Loan Attack Detector
================================================
CLI entry point, output formatting, and alert pipeline integration
for detected flash loan attacks.
"""

import argparse
import asyncio
import json
import logging

from app.core.logging import get_logger
from app.detectors.flash_loan.detector import FlashLoanAttackDetector
from app.detectors.flash_loan.models import AttackSeverity

logger = get_logger(__name__)


def _parse_cli_args() -> argparse.Namespace:
    """Parse command-line arguments for standalone operation."""
    parser = argparse.ArgumentParser(
        description="Flash Loan Attack Detector - Real-time DeFi exploit detection",
    )
    parser.add_argument(
        "--tx",
        type=str,
        default="",
        help="Analyze a specific transaction hash",
    )
    parser.add_argument(
        "--chain",
        type=str,
        default="",
        help="Chain to scan (default: all configured chains)",
    )
    parser.add_argument(
        "--blocks",
        type=int,
        default=10,
        help="Number of recent blocks to scan (default: 10)",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Continuous monitoring mode (scan every 60s)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (default: human-readable)",
    )
    parser.add_argument(
        "--severity",
        type=str,
        default="",
        choices=["info", "low", "medium", "high", "critical"],
        help="Minimum severity level to report (default: all)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose debug logging",
    )
    return parser.parse_args()


def _severity_from_str(s: str) -> AttackSeverity | None:
    """Convert severity string to enum."""
    mapping = {
        "info": AttackSeverity.INFO,
        "low": AttackSeverity.LOW,
        "medium": AttackSeverity.MEDIUM,
        "high": AttackSeverity.HIGH,
        "critical": AttackSeverity.CRITICAL,
    }
    return mapping.get(s.lower())


async def _run_cli() -> None:
    """Execute the CLI workflow."""
    args = _parse_cli_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    detector = FlashLoanAttackDetector()
    chain = args.chain if args.chain else None

    if args.tx:
        logger.info(f"\n🔍 Analyzing transaction: {args.tx}")
        attacks = await detector.analyze_transaction(args.tx, chain or "ethereum")
    else:
        logger.info(f"\n🔍 Scanning last {args.blocks} blocks...")
        report = await detector.scan(blocks_back=args.blocks, chain=chain)
        attacks = report.attacks_confirmed

        if args.json:
            logger.info(
                json.dumps(
                    {
                        "scanned": {
                            "chains": report.chains_scanned,
                            "blocks": report.blocks_scanned,
                            "transactions": report.transactions_analyzed,
                        },
                        "attacks": [a.to_dict() for a in attacks],
                        "duration_seconds": report.duration_seconds,
                    },
                    indent=2,
                )
            )
            return

        logger.info(f"\n{'═' * 60}")
        logger.info(report.summary())
        logger.info(f"{'═' * 60}\n")

    if not attacks:
        logger.info("✅ No flash loan attacks detected.")
        return

    min_severity = _severity_from_str(args.severity) if args.severity else AttackSeverity.INFO
    if min_severity is None:
        min_severity = AttackSeverity.INFO
    severity_order = [
        AttackSeverity.INFO,
        AttackSeverity.LOW,
        AttackSeverity.MEDIUM,
        AttackSeverity.HIGH,
        AttackSeverity.CRITICAL,
    ]
    min_idx = severity_order.index(min_severity)
    filtered = [a for a in attacks if severity_order.index(a.severity) >= min_idx]

    if not filtered:
        logger.info(f"No attacks at severity >= {args.severity or 'info'}")
        return

    for i, attack in enumerate(filtered, 1):
        logger.info(f"\n{'─' * 60}")
        logger.info(f"⚠️  ATTACK #{i}")
        logger.info(f"{'─' * 60}")
        logger.info(f"  Severity:     {attack.severity.value.upper()}")
        logger.info(f"  Type:         {attack.attack_type.value.replace('_', ' ').title()}")
        logger.info(f"  Protocol:     {attack.protocol_used.value.upper()}")
        logger.info(f"  Chain:        {attack.chain}")
        logger.info(f"  Block:        #{attack.block_number}")
        logger.info(f"  Tx Hash:      {attack.tx_hash[:66]}")
        logger.info(f"  Attacker:     {attack.attacker_address}")
        logger.info(f"  Profit:       ${attack.attacker_profit_usd:,.2f}")
        logger.info(f"  Victim Loss:  ${attack.victim_loss_usd:,.2f}")
        logger.info(f"  Confidence:   {attack.confidence:.0%}")
        logger.info(f"  Sophistication: {attack.sophistication_score:.1f}/10")

        if attack.tags:
            logger.info(f"  Tags:         {', '.join(attack.tags)}")
        if attack.targets:
            logger.info(f"  Targets:      {', '.join(a[:16] for a in attack.targets[:3])}")
        if attack.flash_loan_calls:
            logger.info(f"  Flash Loans:  {len(attack.flash_loan_calls)}")
            for fl in attack.flash_loan_calls:
                logger.info(f"    • {fl.summary()}")
        if attack.exploit_timeline:
            logger.info("  Timeline:")
            for event in attack.exploit_timeline[:5]:
                logger.info(f"    → {event}")

    logger.info(f"\n{'═' * 60}")
    logger.info(f"Total: {len(filtered)} attack(s) detected")
    total_impact = sum(a.attacker_profit_usd + a.victim_loss_usd for a in filtered)
    logger.info(f"Total financial impact: ${total_impact:,.2f}")
    logger.info(f"{'═' * 60}")

    if args.monitor:
        logger.info("\n🔄 Continuous monitoring mode. Next scan in 60s...")
        while True:
            await asyncio.sleep(60)
            report = await detector.scan(blocks_back=args.blocks, chain=chain)
            for attack in report.attacks_confirmed:
                logger.info(f"\n⚠️  NEW: {attack.summary()}")


def main() -> None:
    """CLI entry point."""
    asyncio.run(_run_cli())


if __name__ == "__main__":
    main()
