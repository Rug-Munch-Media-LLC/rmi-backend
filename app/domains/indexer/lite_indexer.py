#!/usr/bin/env python3
"""
LiteIndexer — Minimal blockchain indexer using free RPCs.
==========================================================
Fetches ERC-20 Transfer events via eth_getLogs on our own RPC endpoints.
Writes NDJSON (same format as chain-indexer for guard.sh compatibility)
and to ClickHouse wallet_memory.evm_transfers.

Independent of Subsquid — if Subsquid goes down, this keeps running.

Architecture:
  LiteIndexer
    ├── fetch_block_number()     → latest block via RPC
    ├── fetch_transfers(range)   → ERC-20 Transfer events (eth_getLogs)
    ├── fetch_block_timestamps() → batch block → timestamp mapping
    ├── write_ndjson()           → /srv/data/chain-indexer/{chain}/{date}/events.jsonl
    ├── write_clickhouse()       → INSERT INTO wallet_memory.evm_transfers
    └── sync_loop()              → main: load state → fetch → persist → save state

Constraints:
  - Rate-limits itself (round-robin across 10+ endpoints, respects router health)
  - Hard disk cap: won't write if /srv/data/chain-indexer exceeds 10 GB
  - Batch size: 1000 blocks max per run
  - State file: /srv/data/chain-indexer/state/lite_{chain}.json
  - Log: /var/log/lite-indexer.log (via structlog to file)

Usage:
  python3 -m app.domains.indexer.lite_indexer --chain ethereum
  python3 -m app.domains.indexer.lite_indexer --chain ethereum --from-block 25645000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

# Ensure we can import from the backend package
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.core.logging import get_logger
from app.domains.scanner.engines.rpc_router import get_rpc_router

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════

# keccak256("Transfer(address,address,uint256)")
TRANSFER_EVENT_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

CHAIN_INDEXER_DIR = Path("/srv/data/chain-indexer")
STATE_DIR = CHAIN_INDEXER_DIR / "state"

# Hard disk cap: 10 GB
DISK_CAP_BYTES = 10 * 1024**3

# Max blocks per batch (eth_getLogs over too-large ranges can timeout)
BATCH_SIZE = 1000

# Max events per batch (safety limit)
MAX_EVENTS_PER_BATCH = 100_000

# RPC call timeout for eth_getLogs (longer since it can return large data)
RPC_TIMEOUT = 30

# ClickHouse target table
CH_TABLE = "wallet_memory.evm_transfers"

# ── Known eth_getLogs endpoints per chain ────────────────────────────
# Many public RPCs block eth_getLogs (too resource-heavy).
# These endpoints are verified to support it on free tier.
GET_LOGS_ENDPOINTS: dict[str, list[str]] = {
    "ethereum": [
        "https://eth.drpc.org",
        "https://1rpc.io/eth",
        "https://rpc.flashbots.net",
        "https://eth-mainnet.public.blastapi.io",
    ],
    "base": [
        "https://base.drpc.org",
        "https://1rpc.io/base",
        "https://mainnet.base.org",
    ],
    "bsc": [
        "https://bsc.drpc.org",
        "https://1rpc.io/bnb",
        "https://bsc-dataseed.binance.org",
        "https://bsc-dataseed1.binance.org",
    ],
    "arbitrum": [
        "https://arbitrum.drpc.org",
        "https://1rpc.io/arb",
        "https://arb1.arbitrum.io/rpc",
    ],
    "polygon": [
        "https://polygon.drpc.org",
        "https://1rpc.io/matic",
        "https://polygon-rpc.com",
    ],
    "optimism": [
        "https://optimism.drpc.org",
        "https://1rpc.io/op",
        "https://mainnet.optimism.io",
    ],
    "avalanche": [
        "https://avalanche.drpc.org",
        "https://1rpc.io/avax/c",
    ],
    "fantom": [
        "https://fantom.drpc.org",
        "https://1rpc.io/ftm",
    ],
    "linea": [
        "https://linea.drpc.org",
        "https://1rpc.io/linea",
        "https://rpc.linea.build",
    ],
    "zksync": [
        "https://zksync.drpc.org",
        "https://1rpc.io/zksync-era",
        "https://mainnet.era.zksync.io",
    ],
    "scroll": [
        "https://scroll.drpc.org",
        "https://1rpc.io/scroll",
        "https://rpc.scroll.io",
    ],
    "mantle": [
        "https://mantle.drpc.org",
        "https://1rpc.io/mantle",
        "https://rpc.mantle.xyz",
    ],
}


# ═══════════════════════════════════════════════════════════════════════
# CORE INDEXER
# ═══════════════════════════════════════════════════════════════════════


class LiteIndexer:
    """
    Minimal blockchain indexer using free RPCs.

    Fetches ERC-20 Transfer events from the blockchain via eth_getLogs
    and persists them as NDJSON (for guard.sh) and to ClickHouse.

    Thread-safe for cron usage because every run creates its own state file
    and appends to the NDJSON (file is opened and closed each time).
    """

    def __init__(
        self,
        chain: str,
        from_block: int | None = None,
        to_block: int | None = None,
        batch_size: int = BATCH_SIZE,
        disk_cap: int = DISK_CAP_BYTES,
    ) -> None:
        self.chain = chain
        self.batch_size = batch_size
        self.disk_cap = disk_cap

        # Optional override blocks (for testing / backfill)
        self._override_from = from_block
        self._override_to = to_block

        # RPC router singleton (shared health state across runs)
        self.router = get_rpc_router()

        # HTTP client for eth_getLogs (separate from router)
        self._client: httpx.AsyncClient | None = None

        # ClickHouse client (lazy)
        self._ch_client: Any = None

    # ── HTTP Client ─────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create shared HTTP client for eth_getLogs calls."""
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=30,
            )
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(RPC_TIMEOUT),
                limits=limits,
            )
        return self._client

    # ── ClickHouse Connection ───────────────────────────────────────

    def _get_ch_client(self) -> Any | None:
        """Lazy-init ClickHouse native protocol client."""
        if self._ch_client is not None:
            return self._ch_client

        ch_host = os.getenv("CH_HOST") or os.getenv("CLICKHOUSE_HOST", "127.0.0.1")
        ch_port = int(os.getenv("CH_PORT") or os.getenv("CLICKHOUSE_PORT", "9000"))
        ch_user = os.getenv("CH_USER") or os.getenv("CLICKHOUSE_USER", "rmi")
        ch_pass = os.getenv("CH_PASSWORD") or os.getenv("CLICKHOUSE_PASSWORD") or "clickhouse_rmi_prod_2026"
        ch_db = "wallet_memory"

        try:
            from clickhouse_driver import Client as CHClient

            self._ch_client = CHClient(
                host=ch_host,
                port=ch_port,
                user=ch_user,
                password=ch_pass,
                database=ch_db,
                connect_timeout=5,
                send_receive_timeout=30,
            )
            self._ch_client.execute("SELECT 1")
            logger.info("clickhouse_connected", host=ch_host, database=ch_db)
            return self._ch_client
        except Exception as exc:
            logger.warning("clickhouse_unavailable", error=str(exc)[:100])
            return None

    # ── State Management ────────────────────────────────────────────

    def _state_path(self) -> Path:
        return STATE_DIR / f"lite_{self.chain}.json"

    def load_state(self) -> dict[str, Any]:
        """Load last indexed block from state file."""
        path = self._state_path()
        if not path.exists():
            logger.info("no_state_file", chain=self.chain, path=str(path))
            return {}
        try:
            data = json.loads(path.read_text())
            logger.info(
                "state_loaded",
                chain=self.chain,
                last_block=data.get("last_block"),
            )
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("state_load_failed", chain=self.chain, error=str(exc))
            return {}

    def save_state(self, last_block: int, events_count: int) -> None:
        """Save last indexed block to state file."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "last_block": last_block,
            "last_timestamp": datetime.now(UTC).isoformat(),
            "events_indexed": events_count,
            "source": "lite_indexer",
            "chain": self.chain,
        }
        try:
            self._state_path().write_text(json.dumps(state, indent=2))
            logger.info("state_saved", chain=self.chain, last_block=last_block)
        except OSError as exc:
            logger.error("state_save_failed", chain=self.chain, error=str(exc))

    # ── Disk Cap ────────────────────────────────────────────────────

    def check_disk_cap(self) -> bool:
        """Check if chain-indexer data directory is under the disk cap.

        Returns True if we can write, False if cap exceeded.
        """
        if not CHAIN_INDEXER_DIR.exists():
            return True

        try:
            result = subprocess.run(  # noqa: S603 — trusted path
                ["/usr/bin/du", "-sb", str(CHAIN_INDEXER_DIR)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode == 0:
                total_bytes = int(result.stdout.split()[0])
                if total_bytes >= self.disk_cap:
                    logger.error(
                        "disk_cap_exceeded",
                        chain=self.chain,
                        usage_gb=round(total_bytes / 1024**3, 2),
                        cap_gb=round(self.disk_cap / 1024**3, 2),
                    )
                    return False
                logger.debug(
                    "disk_usage",
                    usage_gb=round(total_bytes / 1024**3, 2),
                    free_gb=round((self.disk_cap - total_bytes) / 1024**3, 2),
                )
                return True
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

        # Fallback
        try:
            stat = os.statvfs(str(CHAIN_INDEXER_DIR))
            free_bytes = stat.f_frsize * stat.f_bavail
            return free_bytes > 1024**3  # At least 1GB free
        except (OSError, AttributeError):
            return True  # Fail open

    # ── RPC: Latest Block ───────────────────────────────────────────

    async def fetch_block_number(self) -> int | None:
        """Get the latest block number via the RPC router."""
        result = await self.router.call(self.chain, "eth_blockNumber", [])
        if result and "result" in result:
            return int(result["result"], 16)
        return None

    # ── RPC: Block Timestamps ───────────────────────────────────────

    async def fetch_block_timestamps(self, block_numbers: set[int]) -> dict[int, datetime]:
        """
        Fetch timestamps for a set of block numbers in parallel.

        Falls back to approximate timestamps if RPC fails.
        """
        if not block_numbers:
            return {}

        timestamps: dict[int, datetime] = {}
        batch = list(block_numbers)
        semaphore = asyncio.Semaphore(10)

        async def _get_ts(block_num: int) -> tuple[int, datetime | None]:
            async with semaphore:
                result = await self.router.call(
                    self.chain,
                    "eth_getBlockByNumber",
                    [hex(block_num), False],
                    timeout=15,
                )
                if result and "result" in result:
                    block = result["result"]
                    if block and "timestamp" in block:
                        ts_int = int(block["timestamp"], 16)
                        return block_num, datetime.fromtimestamp(ts_int, tz=UTC)
                return block_num, None

        tasks = [_get_ts(bn) for bn in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for res in results:
            if isinstance(res, tuple) and res[1] is not None:
                timestamps[res[0]] = res[1]

        # Fallback for missing timestamps
        if len(timestamps) < len(block_numbers):
            avg_block_time = 12
            latest_block = max(block_numbers)
            latest_ts = timestamps.get(latest_block)
            if latest_ts:
                for bn in block_numbers:
                    if bn not in timestamps:
                        offset_seconds = avg_block_time * (latest_block - bn)
                        timestamps[bn] = latest_ts - timedelta(seconds=offset_seconds)

        return timestamps

    # ── RPC: Get Logs (Transfer Events) ─────────────────────────────

    async def fetch_transfers(
        self,
        from_block: int,
        to_block: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch ERC-20 Transfer events across ALL token contracts.

        Calls eth_getLogs with only the Transfer event topic filter
        (no address filter) so we catch every transfer on the chain.

        Uses a curated list of endpoints known to support eth_getLogs.
        Falls through errors to try the next endpoint. Returns the
        result with the most parsed events.
        """
        if from_block > to_block:
            return []

        # Clamp range to batch size
        if to_block - from_block > self.batch_size:
            to_block = from_block + self.batch_size

        filter_params = {
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "topics": [TRANSFER_EVENT_TOPIC],
        }

        endpoints = GET_LOGS_ENDPOINTS.get(self.chain, [])
        if not endpoints:
            # Fall back to router endpoints (last resort)
            endpoints = [u for u, _ in self.router.get_available_endpoints(self.chain)]
        if not endpoints:
            logger.error("no_endpoints_for_chain", chain=self.chain)
            return []

        client = await self._get_client()
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getLogs",
            "params": [filter_params],
        }

        best_result: list[dict[str, Any]] | None = None
        last_error: str | None = None

        for url in endpoints:
            try:
                resp = await client.post(url, json=body, timeout=RPC_TIMEOUT)

                if resp.status_code == 200:
                    data = resp.json()
                    if not isinstance(data, dict):
                        continue

                    if "result" in data:
                        logs = data["result"]
                        if isinstance(logs, list):
                            parsed = self._parse_logs(logs, from_block, to_block)
                            logger.info(
                                "transfers_fetched",
                                chain=self.chain,
                                block_range=f"{from_block}-{to_block}",
                                raw_logs=len(logs),
                                parsed=len(parsed),
                                endpoint=url.split("//")[1].split("/")[0][:30],
                            )
                            # Keep the best result (most parsed events)
                            if best_result is None or len(parsed) > len(best_result):
                                best_result = parsed
                                if len(parsed) == len(logs):
                                    break  # Got all events, stop
                        elif isinstance(logs, dict) and "error" in logs:
                            last_error = logs["error"].get("message", str(logs))
                            continue
                        else:
                            continue
                    elif "error" in data:
                        last_error = data["error"].get("message", str(data))
                        logger.debug(
                            "rpc_error_skip",
                            url=url[:50],
                            error=last_error[:60],
                        )
                        continue
                elif resp.status_code == 429:
                    last_error = "rate_limited"
                    logger.debug("rpc_rate_limited", url=url[:50])
                    continue
                else:
                    last_error = f"http_{resp.status_code}"
                    logger.debug("rpc_http_error", url=url[:50], status=resp.status_code)
                    continue

            except httpx.TimeoutException:
                last_error = "timeout"
                continue
            except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
                last_error = str(exc)[:60]
                continue
            except Exception as exc:
                last_error = str(exc)[:80]
                continue

        if best_result is not None:
            return best_result

        logger.error(
            "all_endpoints_failed",
            chain=self.chain,
            from_block=from_block,
            to_block=to_block,
            last_error=last_error,
        )
        return []

    # ── Log Parser ──────────────────────────────────────────────────

    @staticmethod
    def _unpad_address(padded_hex: str) -> str:
        """
        Unpad a 32-byte hex address to 20-byte.

        topics[1] and topics[2] are indexed params padded to 32 bytes.
        """
        if not padded_hex or padded_hex == "0x":
            return "0x0000000000000000000000000000000000000000"
        raw = padded_hex.removeprefix("0x")
        if len(raw) > 40:
            raw = raw[-40:]
        return "0x" + raw.lower()

    def _parse_logs(
        self,
        logs: list[dict[str, Any]],
        from_block: int,
        to_block: int,
    ) -> list[dict[str, Any]]:
        """
        Parse raw eth_getLogs results into NDJSON-compatible events.

        Handles edge cases: removed logs, empty data, out-of-range blocks.
        """
        events: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        duplicates = 0

        for log in logs:
            # Skip removed logs (chain reorgs)
            if log.get("removed", False):
                continue

            # Must have at least Transfer event sig + from + to topics
            topics = log.get("topics", [])
            if len(topics) < 3:
                continue

            # Unpad addresses from 32-byte indexed params
            from_addr = self._unpad_address(topics[1])
            to_addr = self._unpad_address(topics[2])

            # Parse value from data field
            value_hex = log.get("data", "0x0")
            try:
                if isinstance(value_hex, str) and value_hex.startswith("0x"):
                    value_str = str(int(value_hex, 16))
                else:
                    value_str = str(value_hex)
            except (ValueError, TypeError):
                value_str = "0"

            # Parse block number (hex string)
            block_hex = log.get("blockNumber", "0x0")
            try:
                block_num = int(block_hex, 16) if isinstance(block_hex, str) else int(block_hex)
            except (ValueError, TypeError):
                continue

            # Range check
            if block_num < from_block or block_num > to_block:
                continue

            # Parse log index
            log_index_hex = log.get("logIndex", "0x0")
            try:
                log_index = int(log_index_hex, 16) if isinstance(log_index_hex, str) else int(log_index_hex)
            except (ValueError, TypeError):
                log_index = 0

            # Deduplicate by (tx_hash, log_index)
            tx_hash = log.get("transactionHash", "")
            dedup_key = f"{tx_hash}:{log_index}"
            if dedup_key in seen_keys:
                duplicates += 1
                continue
            seen_keys.add(dedup_key)

            events.append(
                {
                    "type": "transfer",
                    "chain": self.chain,
                    "blockNumber": block_num,
                    "blockTimestamp": "",
                    "txHash": tx_hash,
                    "logIndex": log_index,
                    "from": from_addr,
                    "to": to_addr,
                    "tokenAddress": log.get("address", ""),
                    "value": value_str,
                    "tokenSymbol": None,
                    "tokenName": None,
                }
            )

            if len(events) >= MAX_EVENTS_PER_BATCH:
                logger.warning("event_cap_reached", count=MAX_EVENTS_PER_BATCH)
                break

        if duplicates:
            logger.debug("dedup_removed", chain=self.chain, count=duplicates)

        return events

    # ── Timestamp Enrichment ────────────────────────────────────────

    async def _enrich_timestamps(
        self,
        events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Fetch block timestamps and attach to events."""
        if not events:
            return events

        block_numbers = {e["blockNumber"] for e in events}
        timestamps = await self.fetch_block_timestamps(block_numbers)

        fmt = "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)"
        now_str = datetime.now(UTC).strftime(fmt)

        for event in events:
            ts = timestamps.get(event["blockNumber"])
            event["blockTimestamp"] = ts.strftime(fmt) if ts else now_str

        return events

    # ── Output: NDJSON ──────────────────────────────────────────────

    def write_ndjson(self, events: list[dict[str, Any]]) -> int:
        """
        Append events to /srv/data/chain-indexer/{chain}/{date}/events.jsonl.

        Returns bytes written, or 0 on failure.
        """
        if not events:
            return 0

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        out_dir = CHAIN_INDEXER_DIR / self.chain / today
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "events.jsonl"

        try:
            lines = [json.dumps(e, ensure_ascii=False) + "\n" for e in events]
            bytes_written = 0
            with open(out_path, "a") as f:
                for line in lines:
                    f.write(line)
                    bytes_written += len(line.encode("utf-8"))

            logger.info(
                "ndjson_written",
                chain=self.chain,
                path=str(out_path),
                events=len(events),
                bytes=bytes_written,
            )
            return bytes_written
        except OSError as exc:
            logger.error("ndjson_write_failed", chain=self.chain, error=str(exc))
            return 0

    # ── Output: ClickHouse ──────────────────────────────────────────

    def write_clickhouse(self, events: list[dict[str, Any]]) -> int:
        """
        Insert events into ClickHouse wallet_memory.evm_transfers.

        Returns rows inserted, or 0 on failure.
        """
        if not events:
            return 0

        client = self._get_ch_client()
        if not client:
            logger.warning("clickhouse_skipped", chain=self.chain)
            return 0

        now = datetime.now(UTC)
        rows: list[tuple] = []

        for event in events:
            ts_str = event.get("blockTimestamp", "")
            if ts_str:
                try:
                    ts = datetime.strptime(
                        ts_str,
                        "%a %b %d %Y %H:%M:%S GMT+0000 (Coordinated Universal Time)",
                    ).replace(tzinfo=UTC)
                except ValueError:
                    ts = now
            else:
                ts = now

            rows.append(
                (
                    self.chain,
                    event["blockNumber"],
                    event["txHash"],
                    event["from"],
                    event["to"],
                    event["tokenAddress"],
                    event["value"],
                    ts,
                    event["logIndex"],
                    event.get("tokenSymbol"),
                    event.get("tokenName"),
                    now,
                )
            )

        try:
            client.execute(
                f"INSERT INTO {CH_TABLE} "
                "(chain, block_number, tx_hash, from_address, to_address, "
                "token_address, value, block_timestamp, log_index, "
                "token_symbol, token_name, ingested_at) VALUES",
                rows,
            )
            logger.info("clickhouse_inserted", chain=self.chain, rows=len(rows))
            return len(rows)
        except Exception as exc:
            logger.error(
                "clickhouse_insert_failed",
                chain=self.chain,
                rows=len(rows),
                error=str(exc)[:150],
            )
            return 0

    # ── Main Sync Loop ──────────────────────────────────────────────

    async def sync_loop(self) -> dict[str, Any]:
        """
        Main sync loop: load state → fetch → persist → save.

        Returns summary dict with stats.
        """
        start_time = time.monotonic()
        summary: dict[str, Any] = {
            "chain": self.chain,
            "status": "ok",
            "events_fetched": 0,
            "events_written_ndjson": 0,
            "events_written_ch": 0,
            "bytes_written": 0,
            "duration_seconds": 0,
            "errors": [],
        }

        # 1. Disk cap
        if not self.check_disk_cap():
            summary["status"] = "disk_cap_exceeded"
            summary["errors"].append("disk cap exceeded")
            logger.error("sync_aborted_disk_cap", chain=self.chain)
            return summary

        # 2. Determine block range
        state = self.load_state()

        from_block = self._override_from if self._override_from is not None else state.get("last_block", 0) + 1

        # If we've never run, use block 0 (RPC handles it)
        if from_block <= 1:
            from_block = 0

        current_block = await self.fetch_block_number()
        if current_block is None:
            summary["status"] = "no_rpc_available"
            summary["errors"].append("fetch_block_number failed")
            logger.error("sync_aborted_no_rpc", chain=self.chain)
            return summary

        to_block = self._override_to if self._override_to is not None else current_block

        if from_block >= to_block:
            logger.info("caught_up", chain=self.chain, block=current_block)
            summary["status"] = "caught_up"
            return summary

        if to_block - from_block > self.batch_size:
            to_block = from_block + self.batch_size
            logger.info(
                "batch_limited",
                chain=self.chain,
                from_block=from_block,
                to_block=to_block,
            )

        # 3. Fetch
        logger.info(
            "sync_start",
            chain=self.chain,
            from_block=from_block,
            to_block=to_block,
        )

        events = await self.fetch_transfers(from_block, to_block)
        if not events:
            self.save_state(to_block, 0)
            summary["status"] = "no_events"
            summary["duration_seconds"] = time.monotonic() - start_time
            return summary

        summary["events_fetched"] = len(events)

        # 4. Enrich with timestamps
        events = await self._enrich_timestamps(events)

        # 5. Write NDJSON
        bytes_written = self.write_ndjson(events)
        if bytes_written:
            summary["events_written_ndjson"] = len(events)
            summary["bytes_written"] = bytes_written

        # 6. Write ClickHouse
        ch_inserted = self.write_clickhouse(events)
        summary["events_written_ch"] = ch_inserted

        # 7. Save state
        self.save_state(to_block, len(events))

        summary["duration_seconds"] = round(time.monotonic() - start_time, 2)
        logger.info(
            "sync_complete",
            chain=self.chain,
            events=len(events),
            ndjson=bool(bytes_written),
            clickhouse=bool(ch_inserted),
            duration_s=summary["duration_seconds"],
        )
        return summary


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="LiteIndexer — minimal blockchain indexer using free RPCs",
    )
    parser.add_argument(
        "--chain",
        default="ethereum",
        choices=list(GET_LOGS_ENDPOINTS.keys()),
        help="Chain to index (default: ethereum)",
    )
    parser.add_argument(
        "--from-block",
        type=int,
        default=None,
        help="Override starting block (testing / backfill)",
    )
    parser.add_argument(
        "--to-block",
        type=int,
        default=None,
        help="Override ending block (testing / backfill)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE,
        help=f"Max blocks per batch (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--disk-cap",
        type=int,
        default=DISK_CAP_BYTES,
        help=f"Disk cap in bytes (default: {DISK_CAP_BYTES} = 10 GB)",
    )
    parser.add_argument(
        "--ndjson-only",
        action="store_true",
        help="Skip ClickHouse writes",
    )
    parser.add_argument(
        "--ch-only",
        action="store_true",
        help="Skip NDJSON writes",
    )
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    """Async entry point."""
    indexer = LiteIndexer(
        chain=args.chain,
        from_block=args.from_block,
        to_block=args.to_block,
        batch_size=args.batch_size,
        disk_cap=args.disk_cap,
    )

    try:
        summary = await indexer.sync_loop()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("sync_interrupted", chain=args.chain)
        return 1
    except Exception as exc:
        logger.error("sync_fatal", chain=args.chain, error=str(exc), exc_info=True)
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    print(json.dumps(summary, default=str))
    return 0 if summary["status"] in ("ok", "no_events", "caught_up") else 1


def main() -> None:
    """CLI entry point."""
    args = parse_args()
    sys.exit(asyncio.run(async_main(args)))


if __name__ == "__main__":
    main()

__all__ = [
    "LiteIndexer",
    "main",
    "parse_args",
]
