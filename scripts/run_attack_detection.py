#!/usr/bin/env python3
"""
C6 Time-Based Attack Detection — periodic runner.

Called by cron/systemd every 5 minutes. Scans the last hour of blocks in
wallet_memory.dex_events for sandwich attacks, pump & dumps, and flash
loans. Results are persisted to wallet_memory.attack_events, pushed to
Redis (attack_alert:{chain}:{token}), sent to Telegram when confidence is
HIGH, and attacker wallets are marked whale in dex_events.

Scheduling (systemd):
    See infra/attack-detection.timer — OnCalendar=*:0/5

Usage:
    python3 scripts/run_attack_detection.py
    python3 scripts/run_attack_detection.py --chain ethereum --hours-back 2 --no-notify
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def run() -> None:
    """Parse args, run detection once, print a JSON summary."""
    parser = argparse.ArgumentParser(description="C6 Time-Based Attack Detection (ClickHouse-only)")
    parser.add_argument("--chain", default="all", help="Chain name or 'all' (default: all)")
    parser.add_argument("--hours-back", type=int, default=1, help="Scan window in hours (default: 1)")
    parser.add_argument("--no-notify", action="store_true", help="Skip Redis/Telegram/whale wiring")
    parser.add_argument("--force-fresh", action="store_true", help="Bypass caches")
    args = parser.parse_args()

    from app.domains.scanner.engines.attack_detector import run_attack_detection

    result = await run_attack_detection(
        chain=args.chain,
        hours_back=args.hours_back,
        notify=not args.no_notify,
        force_fresh=args.force_fresh,
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(run())
