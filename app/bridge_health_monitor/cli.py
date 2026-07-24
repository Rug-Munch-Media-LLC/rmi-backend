"""
Standalone CLI entry point for bridge health monitoring.

Usage:
    python3 -m app.bridge_health_monitor
    python3 -m app.bridge_health_monitor --bridge stargate
"""

from __future__ import annotations

import asyncio
import logging

from app.bridge_health_monitor.monitor import BridgeHealthMonitor

logger = logging.getLogger(__name__)

async def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Cross-Chain Bridge Health & Exploit Monitor")
    parser.add_argument("--bridge", type=str, default=None, help="Scan a specific bridge key")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--alert", action="store_true", help="Only output if exploit detected (cron-friendly)")
    args = parser.parse_args()

    monitor = BridgeHealthMonitor()
    try:
        if args.alert:
            report = await monitor.alert_if_exploit(args.bridge)
            if report is None:
                logger.debug("[SILENT]")
                return
        else:
            report = await monitor.scan(args.bridge)

        if args.json:
            logger.info(report.to_json())
        else:
            logger.info(report.summary())
    finally:
        await monitor.close()

if __name__ == "__main__":
    asyncio.run(main())

__all__ = ["main"]
