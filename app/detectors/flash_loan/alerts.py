"""
Alert Generation for Flash Loan Attack Detector
================================================
CLI entry point, output formatting, and alert pipeline integration
for detected flash loan attacks.
"""

from __future__ import annotations
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
        logger.info("\n🔍 Analyzing transaction: %s", args.tx)
        attacks = await detector.analyze_transaction(args.tx, chain or "ethereum")
    else:
        logger.info("\n🔍 Scanning last %s blocks...", args.blocks)
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

        logger.info("\n%s", '═' * 60)
        logger.info(report.summary())
        logger.info("%s\n", '═' * 60)

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
        logger.info("No attacks at severity >= %s", args.severity or 'info')
        return

    for i, attack in enumerate(filtered, 1):
        logger.info("\n%s", '─' * 60)
        logger.info("⚠️  ATTACK #%s", i)
        logger.info("%s", '─' * 60)
        logger.info("  Severity:     %s", attack.severity.value.upper())
        logger.info("  Type:         %s", attack.attack_type.value.replace('_', ' ').title())
        logger.info("  Protocol:     %s", attack.protocol_used.value.upper())
        logger.info("  Chain:        %s", attack.chain)
        logger.info("  Block:        #%s", attack.block_number)
        logger.info("  Tx Hash:      %s", attack.tx_hash[:66])
        logger.info("  Attacker:     %s", attack.attacker_address)
        logger.info("  Profit:       $%s", f"{attack.attacker_profit_usd:,.2f}")
        logger.info("  Victim Loss:  $%s", f"{attack.victim_loss_usd:,.2f}")
        logger.info("  Confidence:   %s", f"{attack.confidence:.0%}")
        logger.info("  Sophistication: %s/10", f"{attack.sophistication_score:.1f}")

        if attack.tags:
            logger.info("  Tags:         %s", ', '.join(attack.tags))
        if attack.targets:
            logger.info("  Targets:      %s", ', '.join(a[:16] for a in attack.targets[:3]))
        if attack.flash_loan_calls:
            logger.info("  Flash Loans:  %s", len(attack.flash_loan_calls))
            for fl in attack.flash_loan_calls:
                logger.info("    • %s", fl.summary())
        if attack.exploit_timeline:
            logger.info("  Timeline:")
            for event in attack.exploit_timeline[:5]:
                logger.info("    → %s", event)

    logger.info("\n%s", '═' * 60)
    logger.info("Total: %s attack(s) detected", len(filtered))
    total_impact = sum(a.attacker_profit_usd + a.victim_loss_usd for a in filtered)
    logger.info("Total financial impact: $%s", f"{total_impact:,.2f}")
    logger.info("%s", '═' * 60)

    if args.monitor:
        logger.info("\n🔄 Continuous monitoring mode. Next scan in 60s...")
        while True:
            await asyncio.sleep(60)
            report = await detector.scan(blocks_back=args.blocks, chain=chain)
            for attack in report.attacks_confirmed:
                logger.info("\n⚠️  NEW: %s", attack.summary())

def main() -> None:
    """CLI entry point."""
    asyncio.run(_run_cli())

if __name__ == "__main__":
    main()
