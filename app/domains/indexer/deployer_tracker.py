"""
Deployer Tracker — Detects ALL Contract Deployments (Not Just DEX Pairs)
=======================================================================

Extends the LiteIndexer deployer tracking to detect EVERY contract creation
on a chain, not just DEX PairCreated/PoolCreated events.

Detection Strategy:
  - For each processed block, queries eth_getTransactionReceipt for all
    unique tx hashes found in Transfer events from that block range.
  - Checks if receipt.contractAddress is non-empty (EVM contract creation).
  - Stores to ClickHouse wallet_memory.deployer_events table.

This module runs as a companion to LiteIndexer, processing blocks after
the main sync loop has fetched transfer events.

Usage:
    from app.domains.indexer.deployer_tracker import process_block_deployers

    # After lite_indexer sync, process deployers for a block range
    results = await process_block_deployers(chain, from_block, to_block)

Architecture:
  - Batches tx hashes and queries receipts concurrently (semaphore-limited)
  - Falls back if ClickHouse is unavailable (graceful degradation)
  - Deduplicates against existing deployer_events table
  - Zero external API dependencies (uses same free RPCs as LiteIndexer)
"""

from __future__ import annotations

import asyncio
import functools
import os
import time
from typing import Any

from lite_indexer.logging import get_logger
from lite_indexer.rpc import RpcClient, get_best_endpoint

logger = get_logger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────

# Max concurrent receipt fetches (avoid rate limiting)
_MAX_CONCURRENT = 5

# Max tx hashes per block to check (safety cap for high-tx-volume blocks)
_MAX_TX_PER_BLOCK = 500

# Semaphore for concurrent RPC calls
_semaphore = asyncio.Semaphore(_MAX_CONCURRENT)


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════


async def process_block_deployers(
    chain: str,
    tx_hashes: list[str],
) -> list[dict[str, Any]]:
    """Process a batch of transaction hashes to find contract creations.

    For each tx hash, fetches the transaction receipt and checks if
    it created a new contract.

    Args:
        chain: Chain identifier (ethereum, base, bsc, etc.)
        tx_hashes: List of transaction hashes to check

    Returns:
        List of deployer event dicts:
        [{
            "deployer": "0x...",
            "token_address": "0x...",
            "chain": "ethereum",
            "block_number": 12345678,
            "block_timestamp": "2026-07-30T12:00:00Z",
            "tx_hash": "0x...",
            "type": "contract_creation",
            "factory": "",
        }]
    """
    if not tx_hashes:
        return []

    # Deduplicate
    unique_hashes = list(set(tx_hashes))[:_MAX_TX_PER_BLOCK]
    rpc = RpcClient()

    # Fetch endpoints for this chain
    from lite_indexer.chains import GET_LOGS_ENDPOINTS

    endpoints = GET_LOGS_ENDPOINTS.get(chain, [])
    if not endpoints:
        logger.warning("no_endpoints_for_chain", chain=chain)
        return []

    async def _check_receipt(tx_hash: str) -> dict[str, Any] | None:
        """Fetch receipt for a single tx and extract contract creation."""
        async with _semaphore:
            for attempt in range(2):
                try:
                    endpoint = get_best_endpoint(endpoints)

                    # Use generic call for eth_getTransactionReceipt
                    receipt_raw = await rpc.call(endpoint, "eth_getTransactionReceipt", [tx_hash])
                    if not receipt_raw:
                        return None
                    receipt = receipt_raw.get("result", {})
                    if not isinstance(receipt, dict):
                        return None

                    contract_addr = receipt.get("contractAddress", "")
                    if not contract_addr or contract_addr == "0x":
                        return None

                    from_addr = receipt.get("from", "")
                    block_num_hex = receipt.get("blockNumber", "0x0")
                    block_num = int(block_num_hex, 16) if isinstance(block_num_hex, str) else int(block_num_hex)

                    # Get block timestamp
                    block_data = await rpc.eth_get_block_by_number(endpoint, block_num)
                    timestamp = ""
                    if block_data:
                        ts_raw = block_data.get("timestamp", "0x0")
                        ts_val = int(ts_raw, 16) if isinstance(ts_raw, str) else int(ts_raw or 0)
                        if ts_val > 0:
                            from datetime import UTC, datetime

                            timestamp = datetime.fromtimestamp(ts_val, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

                    return {
                        "deployer": from_addr.lower() if from_addr else "",
                        "token_address": contract_addr.lower(),
                        "chain": chain,
                        "block_number": block_num,
                        "block_timestamp": timestamp,
                        "tx_hash": tx_hash,
                        "type": "contract_creation",
                        "factory": "",
                    }
                except Exception as exc:
                    if attempt == 0:
                        await asyncio.sleep(1)
                        continue
                    logger.debug("receipt_fetch_failed", tx=tx_hash[:16], error=str(exc)[:60])
                    return None

    # Process all tx hashes concurrently with semaphore
    tasks = [_check_receipt(tx) for tx in unique_hashes]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    deployer_events = []
    for r in results:
        if isinstance(r, dict) and r.get("deployer") and r.get("token_address"):
            deployer_events.append(r)

    logger.info(
        "deployer_tracker_processed",
        chain=chain,
        checked=len(unique_hashes),
        found=len(deployer_events),
    )

    return deployer_events


async def write_deployer_events(
    chain: str,
    events: list[dict[str, Any]],
) -> int:
    """Write deployer events to ClickHouse.

    Args:
        chain: Chain identifier
        events: List of deployer event dicts from process_block_deployers

    Returns:
        Number of events written
    """
    if not events:
        return 0

    try:
        from clickhouse_driver import Client as CHClient

        ch_host = os.getenv("CH_HOST") or os.getenv("CLICKHOUSE_HOST", "127.0.0.1")
        ch_port = int(os.getenv("CH_PORT") or os.getenv("CLICKHOUSE_PORT", "9000"))
        ch_user = os.getenv("CH_USER") or os.getenv("CLICKHOUSE_USER", "default")
        ch_pass = os.getenv("CH_PASSWORD") or os.getenv("CLICKHOUSE_PASSWORD") or ""
        ch_db = os.getenv("CH_DATABASE", "wallet_memory")

        client = CHClient(
            host=ch_host,
            port=ch_port,
            user=ch_user,
            password=ch_pass,
            database=ch_db,
            connect_timeout=5,
            send_receive_timeout=30,
        )

        from datetime import UTC, datetime

        now = datetime.now(UTC)

        # Deduplicate against existing deployer_events
        existing_rows = client.execute(
            "SELECT DISTINCT token_address FROM wallet_memory.deployer_events WHERE chain = %(chain)s",
            {"chain": chain},
        )
        existing_tokens = {r[0].lower() for r in existing_rows}
        new_events = [e for e in events if e.get("token_address", "").lower() not in existing_tokens]

        if not new_events:
            logger.info("deployer_events_all_duplicates", chain=chain)
            return 0

        rows = []
        for e in new_events:
            ts_str = e.get("block_timestamp", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    ts = now
            else:
                ts = now

            rows.append(
                (
                    e.get("deployer", ""),
                    e.get("token_address", ""),
                    chain,
                    e.get("block_number", 0),
                    ts,
                    e.get("tx_hash", ""),
                    e.get("type", "contract_creation"),
                    e.get("factory", ""),
                    now,
                    "deployer_tracker",
                )
            )

        client.execute(
            "INSERT INTO wallet_memory.deployer_events "
            "(deployer, token_address, chain, block_number, block_timestamp, "
            "tx_hash, type, factory, ingested_at, source) VALUES",
            rows,
        )

        logger.info(
            "deployer_events_written",
            chain=chain,
            rows=len(rows),
            skipped=len(events) - len(rows),
        )
        return len(rows)

    except ImportError:
        logger.warning("clickhouse_driver not available for deployer events")
        return 0
    except Exception as exc:
        logger.error("deployer_events_write_failed", error=str(exc)[:150])
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ═══════════════════════════════════════════════════════════════════════════


async def run_deployer_tracker(
    chain: str,
    from_block: int | None = None,
    to_block: int | None = None,
    batch_size: int = 1000,
    start_from_head: bool = True,
) -> dict[str, Any]:
    """Run deployer tracker as a standalone sweep.

    Scans recent blocks, fetches transaction receipts for Transfer
    events, and records any contract creations.

    Args:
        chain: Chain to scan
        from_block: Starting block (None = use state)
        to_block: Ending block (None = chain tip)
        batch_size: Max blocks per batch
        start_from_head: Start from chain tip if True

    Returns:
        Summary dict
    """
    start_time = time.monotonic()
    summary: dict[str, Any] = {
        "chain": chain,
        "status": "ok",
        "blocks_scanned": 0,
        "deployers_found": 0,
        "deployers_written": 0,
        "duration_seconds": 0,
    }

    from lite_indexer import LiteIndexer

    indexer = LiteIndexer(
        chain=chain, from_block=from_block, to_block=to_block, batch_size=batch_size, start_from_head=start_from_head
    )

    # Determine block range
    block_range = await indexer._determine_block_range(summary)
    if block_range is None:
        return summary

    from_block_range, to_block_range = block_range
    if to_block_range - from_block_range > batch_size:
        to_block_range = from_block_range + batch_size

    summary["blocks_scanned"] = to_block_range - from_block_range

    # Fetch transfer events to get tx hashes
    events = await indexer.fetch_transfers(from_block_range, to_block_range)
    if not events:
        summary["status"] = "no_events"
        summary["duration_seconds"] = round(time.monotonic() - start_time, 2)
        return summary

    tx_hashes = list(
        {
            e.get("txHash", "") or e.get("transactionHash", "")
            for e in events
            if e.get("txHash") or e.get("transactionHash")
        }
    )

    if not tx_hashes:
        summary["status"] = "no_tx_hashes"
        summary["duration_seconds"] = round(time.monotonic() - start_time, 2)
        return summary

    # Process deployers
    deployer_events = await process_block_deployers(chain, tx_hashes)
    summary["deployers_found"] = len(deployer_events)

    if deployer_events:
        # Enrich with timestamps
        deployer_events = await indexer._enrich_timestamps(deployer_events)
        # write_deployer_events is sync (clickhouse_driver), run in thread
        written = await asyncio.to_thread(
            functools.partial(write_deployer_events, chain, deployer_events),
        )
        summary["deployers_written"] = written

    summary["duration_seconds"] = round(time.monotonic() - start_time, 2)
    logger.info(
        "deployer_tracker_complete",
        chain=chain,
        blocks=summary["blocks_scanned"],
        found=summary["deployers_found"],
        written=summary["deployers_written"],
        duration_s=summary["duration_seconds"],
    )

    return summary


# ═══════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════
# Provides a schedulable entry point (systemd timer / cron):
#   python3 -m app.domains.indexer.deployer_tracker --chains ethereum,base,bsc
#   python3 -m app.domains.indexer.deployer_tracker --chain ethereum --from-block 25645000 --to-block 25646000
#
# The default is a periodic head sweep: each run scans (tip - 100) → tip for
# contract creations and inserts them into wallet_memory.deployer_events.


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the deployer tracker sweep."""
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Deployer Tracker — detect contract deployments via tx receipts",
    )
    parser.add_argument(
        "--chain",
        default=None,
        help="Single chain to sweep (e.g. ethereum). Overrides --chains.",
    )
    parser.add_argument(
        "--chains",
        default="ethereum",
        help="Comma-separated chains to sweep (default: ethereum)",
    )
    parser.add_argument(
        "--from-block",
        type=int,
        default=None,
        help="Override starting block (None = tip - HEAD_BUFFER)",
    )
    parser.add_argument(
        "--to-block",
        type=int,
        default=None,
        help="Override ending block (None = chain tip)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Max blocks per batch (default: 1000)",
    )
    parser.add_argument(
        "--start-from-head",
        action="store_true",
        default=False,
        help="Scan from chain tip instead of shared state (recommended for periodic sweeps)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON summary instead of structured logs",
    )
    args = parser.parse_args(argv)

    chains = [args.chain] if args.chain else [c.strip() for c in args.chains.split(",") if c.strip()]
    # Periodic sweeps always start from the head — the shared indexer state
    # file belongs to the main indexer and must not be consumed by this sweep.
    start_from_head = args.start_from_head or (args.from_block is None and args.to_block is None)

    results: list[dict[str, Any]] = []
    exit_code = 0
    for chain in chains:
        try:
            summary = asyncio.run(
                run_deployer_tracker(
                    chain,
                    from_block=args.from_block,
                    to_block=args.to_block,
                    batch_size=args.batch_size,
                    start_from_head=start_from_head,
                )
            )
        except KeyboardInterrupt:
            logger.info("deployer_tracker_interrupted")
            return 130
        except Exception as exc:
            logger.error("deployer_tracker_failed", chain=chain, error=str(exc))
            summary = {"chain": chain, "status": "error", "error": str(exc)}
            exit_code = 1
        results.append(summary)
        if summary.get("status") not in ("ok", "no_events", "no_tx_hashes"):
            exit_code = 1

    if args.json:
        print(json.dumps(results, default=str))
    return exit_code


if __name__ == "__main__":
    import sys

    sys.exit(main())
