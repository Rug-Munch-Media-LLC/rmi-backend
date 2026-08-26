#!/usr/bin/env python3
"""Backfill deployer_history outcomes — ground truth for scanner backtesting.

For every deployer_history row with outcome='unknown':
  1. lifespan_days = (last_transfer_block_time - deployed_at) in days,
     computed from wallet_memory.evm_transfers for that token.
  2. outcome label:
       - "likely_rug"   : liquidity window < 3 days (fast death)
       - "short_lived"  : 3–30 days
       - "established"  : > 90 days of continuous transfers
       - "active"       : transfers within the last 24h
  3. is_scam_related = 1 when likely_rug AND the deployer has ≥1 other
     likely_rug token (serial deployer pattern).

Run inside the rmi-backend container (has clickhouse_driver + creds):
    python scripts/backfill_deployer_outcomes.py [--limit 2000] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

from clickhouse_driver import Client


def ch():
    return Client(
        host=os.getenv("CLICKHOUSE_HOST", "rmi-clickhouse"),
        port=int(os.getenv("CLICKHOUSE_PORT", "9000")),
        user=os.getenv("CLICKHOUSE_USER", "rmi"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        database=os.getenv("CLICKHOUSE_DATABASE", "wallet_memory"),
    )


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=2000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    client = ch()
    rows = client.execute(
        """
        SELECT token_address, chain_id, deployed_at
        FROM wallet_memory.deployer_history
        WHERE outcome = 'unknown'
        ORDER BY deployed_at DESC
        LIMIT %(n)s
        """,
        {"n": args.limit},
    )
    print(f"rows to process: {len(rows)}")
    if not rows:
        return 0

    t0 = time.time()
    updates = []
    serial_counter: dict[str, int] = {}

    # Pass 1: lifespan per token (bounded scan via token filter)
    for i, (token, chain_id, deployed_at) in enumerate(rows):
        try:
            q = client.execute(
                """
                SELECT min(block_timestamp), max(block_timestamp)
                FROM wallet_memory.evm_transfers
                WHERE token_address = lower(%(t)s)
                  AND block_timestamp >= toDateTime(%(dep)s) - INTERVAL 1 DAY
                """,
                {"t": token, "dep": int(deployed_at.timestamp())},
            )
            r = q
            if not r or r[0][0] is None:
                continue
            first_ts, last_ts = r[0]
            life = max(0.0, (last_ts - deployed_at).total_seconds() / 86400)
            hours_active = max(0.0, (last_ts - first_ts).total_seconds() / 3600)
            if last_ts > datetime_now_minus_24h():
                outcome = "active"
            elif life < 3:
                outcome = "likely_rug"
            elif life < 30:
                outcome = "short_lived"
            elif life >= 90:
                outcome = "established"
            else:
                outcome = "medium_life"
            updates.append((outcome, round(life, 2), token, chain_id))
        except Exception as exc:
            if i % 200 == 0:
                print(f"  err@{i}: {str(exc)[:80]}")
        if i % 250 == 0 and i:
            print(f"  …{i} scanned ({time.time() - t0:.0f}s)")

    print(f"labeled: {len(updates)}")

    # Pass 2: serial-deployer scam flag
    rug_by_deployer: dict[str, int] = {}
    for outcome, _life, token, chain_id in updates:
        if outcome != "likely_rug":
            continue
        owner = client.execute(
            """
            SELECT deployer_address FROM wallet_memory.deployer_history
            WHERE token_address = %(t)s AND chain_id = %(c)s LIMIT 1
            """,
            {"t": token, "c": chain_id},
        )
        if owner:
            rug_by_deployer[owner[0][0]] = rug_by_deployer.get(owner[0][0], 0) + 1

    serial = {d for d, n in rug_by_deployer.items() if n >= 2}
    print(f"serial-rug deployers detected: {len(serial)}")

    if args.dry_run:
        from collections import Counter

        print("outcome distribution:", dict(Counter(u[0] for u in updates)))
        return 0

    # Apply updates (mutation-light: one ALTER per row is fine at this scale;
    # batched via INSERT overwriting would need ReplacingMergeTree — keep simple)
    done = 0
    for outcome, life, token, chain_id in updates:
        scam = 0
        owner_rows = client.execute(
            "SELECT deployer_address FROM wallet_memory.deployer_history "
            "WHERE token_address = %(t)s AND chain_id = %(c)s LIMIT 1",
            {"t": token, "c": chain_id},
        )
        if owner_rows and owner_rows[0][0] in serial and outcome == "likely_rug":
            scam = 1
        client.command(
            """
            ALTER TABLE wallet_memory.deployer_history
            UPDATE outcome = %(o)s, lifespan_days = %(l)s, is_scam_related = %(s)s
            WHERE token_address = %(t)s AND chain_id = %(c)s AND outcome = 'unknown'
            """,
            {"o": outcome, "l": life, "s": scam, "t": token, "c": chain_id},
        )
        done += 1
    print(f"updated: {done}")
    return 0


def datetime_now_minus_24h():
    # clickhouse_driver returns NAIVE datetimes (UTC) — compare naive-to-naive
    from datetime import datetime, timedelta

    return datetime.utcnow() - timedelta(hours=24)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
