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
import contextlib
import fcntl
import json
import os
import random
import shutil
import signal
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

# keccak256("Swap(address,uint256,uint256,uint256,uint256,address)")
SWAP_V2_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
# keccak256("Swap(address,address,int256,int256,uint160,uint128,int24)")
SWAP_V3_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

# keccak256("PairCreated(address,address,address,uint256)")
PAIR_CREATED_TOPIC = "0x79b58832c15b09feb3df60e6992ac2bf90bb88a193b76dcf74a5529cfc6a96e2"

# keccak256("PoolCreated(address,address,uint24,int24,address)")
POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"

# Known DEX V2 factory addresses for PairCreated event queries
# These let us build a pair→(token0, token1) mapping from factory logs.
FACTORY_ADDRESSES: dict[str, list[str]] = {
    "ethereum": ["0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"],  # Uniswap V2
    "base": ["0x8909Dc15e40173Ff4699343b6eB8132c65e18eC6"],
    "bsc": ["0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"],  # PancakeSwap
    "polygon": ["0x5757371414417b8C6CAad45bAeF941aBc7d3Ab32"],  # QuickSwap
    "arbitrum": ["0xf1D7CC64Fb4452F05c498126312eBE29f30Fbcf9"],
}

# Known DEX V3 factory addresses for PoolCreated event queries
# V3 PoolCreated events carry token0 (topic[1]), token1 (topic[2]), fee (topic[3]),
# tickSpacing (data[0:32]), and pool address (data[32:64]).
V3_FACTORY_ADDRESSES: dict[str, list[str]] = {
    "ethereum": ["0x1F98431c8aD98523631AE4a59f267346ea31F984"],  # Uniswap V3
    "base": ["0x33128a8fC17869897dcE68Ed026d694621f6FDfD"],
    "bsc": ["0xdB1d10011AD0Ff90774D0C6Bb92e5C5c8b4461F7"],  # PancakeSwap V3
    "arbitrum": ["0x1F98431c8aD98523631AE4a59f267346ea31F984"],
    "optimism": ["0x1F98431c8aD98523631AE4a59f267346ea31F984"],
    "polygon": ["0x1F98431c8aD98523631AE4a59f267346ea31F984"],
}

# keccak256("token0()")[:4] and keccak256("token1()")[:4] — eth_call selectors
TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"

# Max concurrent eth_call fallback requests for pool token resolution
MAX_CONCURRENT_POOL_RESOLVE = 5

CHAIN_INDEXER_DIR = Path("/srv/data/chain-indexer")
STATE_DIR = CHAIN_INDEXER_DIR / "state"

# Pair cache template: STATE_DIR / "pair_tokens_{chain}.json"
# Persists pair→(token0, token1) mapping across restarts so we never lose
# token resolution for the 47M+ dex_events already in ClickHouse.

# Hard disk cap: 10 GB
DISK_CAP_BYTES = 10 * 1024**3

# Max blocks per batch (eth_getLogs over too-large ranges can timeout)
BATCH_SIZE = 1000

# Max events per batch (safety limit)
MAX_EVENTS_PER_BATCH = 100_000

# Blocks behind chain tip when --start-from-head is used
# We start from (tip - HEAD_BUFFER) so we catch any in-flight blocks
HEAD_BUFFER = 100

# RPC call timeout for eth_getLogs (longer since it can return large data)
RPC_TIMEOUT = 30

# Max backoff for recently-failed endpoints (seconds)
BACKOFF_CAP = 8

# Max wait for rate-limited endpoints (seconds)
RATE_LIMIT_WAIT_CAP = 16

# Default timeout for _rpc_call_with_backoff
RPC_CALL_TIMEOUT = 15.0

# Timeout for block timestamp fetch calls
TIMESTAMP_FETCH_TIMEOUT = 15

# Semaphore limit for parallel timestamp fetches
GET_LOGS_SEMAPHORE_LIMIT = 10

# Minimum free disk space (bytes) — 1GB
MIN_FREE_DISK_BYTES = 1_000_000_000

# ClickHouse target tables
CH_TABLE = "wallet_memory.evm_transfers"
CH_DEX_TABLE = "wallet_memory.dex_events"

# ── Known eth_getLogs endpoints per chain ────────────────────────────
# Many public RPCs block eth_getLogs (too resource-heavy).
# These endpoints are verified to support it on free tier.
#
# Tier organisation (3 tiers per chain):
#   TIER 1 (indices 0-1) — fastest, most reliable (drpc.org, official RPCs)
#   TIER 2 (indices 2-3) — fallbacks (1rpc.io, publicnode.com, etc.)
#   TIER 3 (indices 4+)  — last resort (gateway.tatum.io, quiknode.pro, etc.)
#
# Tested 2026-07-30 against eth_getLogs fromBlock=0x1, toBlock=0x64:
#   ✅ = returned result   ⏳ = rate-limited   ❌ = removed from tier 1/2
GET_LOGS_ENDPOINTS: dict[str, list[str]] = {
    # ── Ethereum ───────────────────────────────────────────────────────
    # ✅ eth.drpc.org  ✅ rpc.mevblocker.io  ⏳ 1rpc.io/eth
    "ethereum": [
        "https://eth.drpc.org",  # T1 ✅
        "https://rpc.mevblocker.io",  # T1 ✅
        "https://1rpc.io/eth",  # T2 ⏳
        "https://eth-mainnet.public.blastapi.io",  # T2 ⏳ 10-block limit
    ],
    # ── Base ──────────────────────────────────────────────────────────
    # ✅ base.drpc.org  ✅ mainnet.base.org  ⏳ 1rpc.io/base
    "base": [
        "https://base.drpc.org",  # T1 ✅
        "https://mainnet.base.org",  # T1 ✅
        "https://1rpc.io/base",  # T2 ⏳
        "https://base-rpc.publicnode.com",  # T2 sometimes needs token
    ],
    # ── BSC ───────────────────────────────────────────────────────────
    # ✅ nodereal.io  ⏳ bsc.drpc.org  ✅ tatum.io  ⏳ binance.org
    "bsc": [
        "https://bsc-mainnet.nodereal.io/v1/64a9df0874fb4a93b9d0a3849de012d3",  # T1 ✅
        "https://bsc.drpc.org",  # T1 ⏳
        "https://bsc-mainnet.gateway.tatum.io",  # T2 ✅
        "https://bsc-dataseed1.binance.org",  # T2 ⏳
    ],
    # ── Polygon ──────────────────────────────────────────────────────
    # ✅ polygon.drpc.org  ✅ quiknode.pro  ✅ tatum.io  ⏳ 1rpc.io/matic
    "polygon": [
        "https://polygon.drpc.org",  # T1 ✅
        "https://rpc-mainnet.matic.quiknode.pro",  # T1 ✅
        "https://polygon-mainnet.gateway.tatum.io",  # T2 ✅
        "https://1rpc.io/matic",  # T2 ⏳
    ],
    # ── Arbitrum ─────────────────────────────────────────────────────
    # ✅ arb1.arbitrum.io/rpc  ❌ arbitrum.drpc.org (10-block limit)  ⏳ 1rpc.io/arb
    "arbitrum": [
        "https://arb1.arbitrum.io/rpc",  # T1 ✅ official — best
        "https://arbitrum.drpc.org",  # T1 ⚠️ 10-block getLogs limit
        "https://1rpc.io/arb",  # T2 ⏳
    ],
    # ── Optimism ─────────────────────────────────────────────────────
    # ✅ mainnet.optimism.io  ❌ optimism.drpc.org (upstream)  ⏳ 1rpc.io/op
    "optimism": [
        "https://mainnet.optimism.io",  # T1 ✅ official
        "https://optimism.drpc.org",  # T1 ⚠️ intermittent
        "https://1rpc.io/op",  # T2 ⏳
    ],
    # ── Avalanche ────────────────────────────────────────────────────
    # ✅ api.avax.network  ❌ avalanche.drpc.org (upstream)  ⏳ 1rpc.io/avax/c
    "avalanche": [
        "https://api.avax.network/ext/bc/C/rpc",  # T1 ✅ official
        "https://avalanche.drpc.org",  # T1 ⚠️ intermittent
        "https://1rpc.io/avax/c",  # T2 ⏳
    ],
    # ── Fantom ───────────────────────────────────────────────────────
    # ✅ fantom.drpc.org  ✅ rpc.fantom.network  ✅ rpcapi.fantom.network  ⏳ 1rpc.io/ftm
    "fantom": [
        "https://fantom.drpc.org",  # T1 ✅
        "https://rpc.fantom.network",  # T1 ✅
        "https://rpcapi.fantom.network",  # T2 ✅
        "https://1rpc.io/ftm",  # T2 ⏳
    ],
    # ── Linea ────────────────────────────────────────────────────────
    # ✅ rpc.linea.build  ✅ linea.drpc.org  ⏳ 1rpc.io/linea
    "linea": [
        "https://rpc.linea.build",  # T1 ✅ official
        "https://linea.drpc.org",  # T1 ✅
        "https://1rpc.io/linea",  # T2 ⏳
    ],
    # ── Scroll ───────────────────────────────────────────────────────
    # ✅ rpc.scroll.io  ✅ scroll.drpc.org  ⏳ 1rpc.io/scroll
    "scroll": [
        "https://rpc.scroll.io",  # T1 ✅ official
        "https://scroll.drpc.org",  # T1 ✅
        "https://1rpc.io/scroll",  # T2 ⏳
    ],
    # ── zkSync Era ───────────────────────────────────────────────────
    # ✅ mainnet.era.zksync.io  ❌ zksync.drpc.org (height)  ⏳ 1rpc.io/zksync2-era
    "zksync": [
        "https://mainnet.era.zksync.io",  # T1 ✅ official — best
        "https://zksync.drpc.org",  # T1 ⚠️ intermittent
        "https://1rpc.io/zksync2-era",  # T2 ⏳
    ],
    # ── Mantle ───────────────────────────────────────────────────────
    # ✅ rpc.mantle.xyz  ✅ mantle.drpc.org  ⏳ 1rpc.io/mantle
    "mantle": [
        "https://rpc.mantle.xyz",  # T1 ✅ official
        "https://mantle.drpc.org",  # T1 ✅
        "https://1rpc.io/mantle",  # T2 ⏳
    ],
    # ── Gnosis ───────────────────────────────────────────────────────
    "gnosis": [
        "https://gnosis.drpc.org",  # T1 ✅
        "https://rpc.gnosischain.com",  # T1 ✅
        "https://1rpc.io/gnosis",  # T2 ⏳
    ],
}

# ── Chain-specific block time (seconds) for adaptive polling ──────────
CHAIN_POLL_INTERVALS: dict[str, float] = {
    "ethereum": 12.0,
    "base": 2.0,
    "bsc": 3.0,
    "polygon": 2.0,
    "arbitrum": 0.25,
    "optimism": 2.0,
    "avalanche": 2.0,
    "linea": 12.0,
    "scroll": 3.0,
    "zksync": 1.0,
    "mantle": 2.0,
    "gnosis": 5.0,
}

# ── Endpoint health tracking ──────────────────────────────────────────
_endpoint_failures: dict[str, list[float]] = {}
_ENDPOINT_COOLDOWN = 300  # 5 min cooldown after 3 failures
_MAX_FAILURES = 3

# ── Prometheus-style metrics counters ─────────────────────────────────
_metrics: dict[str, int] = {
    "blocks_processed": 0,
    "transfers_written": 0,
    "dex_swaps_written": 0,
    "rpc_errors": 0,
    "rpc_rate_limits": 0,
    "clickhouse_inserts": 0,
    "clickhouse_errors": 0,
}

# ── Session-level event dedup ────────────────────────────────────────
_seen_events: set[tuple[str, int]] = set()


# ═══════════════════════════════════════════════════════════════════════
# CORE INDEXER
# ═══════════════════════════════════════════════════════════════════════


# ── Endpoint Health ──────────────────────────────────────────────────


def _get_best_endpoint(endpoints: list[str]) -> str:
    """
    Return the healthiest endpoint with load-balanced rotation.

    Scoring:
      - Each recent failure adds 1000 penalty (dominates)
      - Original list position adds 1..N penalty (prefers earlier tiers)
      - Bottom 30% of healthy list is ignored for rotation diversity
      - Top 2 healthy endpoints are chosen randomly for load distribution

    Fallthrough: if all endpoints are in cooldown, clear and retry the first.
    """
    now = time.time()

    # Prune stale failures and score endpoints
    scored: list[tuple[int, str]] = []
    for i, ep in enumerate(endpoints):
        failures = [t for t in _endpoint_failures.get(ep, []) if now - t < _ENDPOINT_COOLDOWN]
        _endpoint_failures[ep] = failures
        penalty = len(failures) * 1000 + i  # failures dominate, then position
        scored.append((penalty, ep))

    scored.sort(key=lambda x: x[0])

    # Healthy endpoints: those with < 3 recent failures
    healthy = [ep for penalty, ep in scored if penalty < _MAX_FAILURES * 1000]

    if not healthy:
        # All endpoints in cooldown — clear and retry the first
        for ep in endpoints:
            _endpoint_failures[ep] = []
        logger.warning("all_endpoints_cooldown_reset", count=len(endpoints))
        return endpoints[0]

    # Load distribution: pick randomly from top half of healthy endpoints
    pool_size = max(2, len(healthy) // 2)
    return random.choice(healthy[:pool_size])  # noqa: S311


def _record_endpoint_failure(endpoint: str) -> None:
    """Record a failure for this endpoint."""
    _endpoint_failures.setdefault(endpoint, []).append(time.time())


async def _apply_endpoint_backoff(url: str) -> None:
    """Apply exponential backoff if this endpoint has recent failures."""
    recent_failures = [t for t in _endpoint_failures.get(url, []) if time.time() - t < _ENDPOINT_COOLDOWN]
    if recent_failures:
        backoff = 2 ** len(recent_failures)
        await asyncio.sleep(min(backoff, BACKOFF_CAP))


async def _handle_rate_limit(url: str) -> None:
    """Record a rate-limit failure and sleep with exponential backoff."""
    _record_endpoint_failure(url)
    _metrics["rpc_rate_limits"] += 1
    wait = min(2 ** len(_endpoint_failures.get(url, [])), RATE_LIMIT_WAIT_CAP)
    logger.debug("rpc_rate_limited", url=url[:50], backoff=wait)
    await asyncio.sleep(wait)


# ── RPC Call with Backoff ────────────────────────────────────────────


async def _rpc_call_with_backoff(endpoint: str, payload: dict, max_retries: int = 3) -> dict:
    """Call RPC with exponential backoff on rate limits."""
    for attempt in range(max_retries):
        try:
            async with httpx.AsyncClient(timeout=RPC_CALL_TIMEOUT) as client:
                resp = await client.post(endpoint, json=payload)
                if resp.status_code == 429:
                    wait = 2**attempt
                    logger.warning(
                        "rpc_rate_limited_backoff",
                        endpoint=endpoint[:40],
                        attempt=attempt + 1,
                        wait=wait,
                    )
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
        except Exception:
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            raise


# ── Session-Level Event Dedup ────────────────────────────────────────


def _is_duplicate(tx_hash: str, log_index: int) -> bool:
    """Check if this (tx_hash, log_index) was already seen this session."""
    key = (tx_hash, log_index)
    if key in _seen_events:
        return True
    _seen_events.add(key)
    return False


# ── Disk Space Check ─────────────────────────────────────────────────


def _check_disk_space(min_free_bytes: int = MIN_FREE_DISK_BYTES) -> bool:
    """Check if there's enough disk space to continue.

    Returns True if OK, False if dangerously low.
    """
    try:
        usage = shutil.disk_usage("/srv/data")
        free = usage.free
        if free < min_free_bytes:
            logger.error(
                "disk_full",
                free_gb=round(free / 1e9, 2),
                min_gb=round(min_free_bytes / 1e9, 2),
            )
            return False
        logger.debug("disk_space_ok", free_gb=round(free / 1e9, 2))
        return True
    except OSError as exc:
        logger.warning("disk_space_check_failed", error=str(exc)[:80])
        return True  # Fail open


# ── Metrics Logger ───────────────────────────────────────────────────


def _log_metrics() -> None:
    """Log current metrics counters and reset them."""
    logger.info("lite_indexer_metrics", **_metrics)
    for k in _metrics:
        _metrics[k] = 0


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
        start_from_head: bool = False,
    ) -> None:
        self.chain = chain
        self.batch_size = batch_size
        self.disk_cap = disk_cap
        self.dex_enabled = False
        self.start_from_head = start_from_head

        # Pair→(token0, token1) cache built from PairCreated/PoolCreated events
        # Persisted to disk so token resolution survives restarts
        self._pair_tokens: dict[str, tuple[str, str]] = {}
        self._load_pair_cache()

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
        # When --start-from-head is active, ignore any stale state file
        # and let _determine_block_range pick up from chain tip.
        if self.start_from_head:
            logger.info("state_skipped_start_from_head", chain=self.chain)
            return {}

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
        """Save last indexed block to state file (with file locking for racy safety)."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "last_block": last_block,
            "last_timestamp": datetime.now(UTC).isoformat(),
            "events_indexed": events_count,
            "source": "lite_indexer",
            "chain": self.chain,
        }
        try:
            path = self._state_path()
            with open(path, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                f.write(json.dumps(state, indent=2))
                fcntl.flock(f, fcntl.LOCK_UN)
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
        semaphore = asyncio.Semaphore(GET_LOGS_SEMAPHORE_LIMIT)

        async def _get_ts(block_num: int) -> tuple[int, datetime | None]:
            async with semaphore:
                result = await self.router.call(
                    self.chain,
                    "eth_getBlockByNumber",
                    [hex(block_num), False],
                    timeout=TIMESTAMP_FETCH_TIMEOUT,
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
            _metrics["rpc_errors"] += 1
            return []

        # Rotate starting endpoint for load distribution across runs.
        #   _get_best_endpoint() scores endpoints by failure count x 1000
        #   + list position, then picks randomly from the top half of
        #   healthy endpoints — giving us both failure-awareness and
        #   natural load spreading across Tier-1 providers.
        start_idx = endpoints.index(_get_best_endpoint(endpoints))
        rotated = endpoints[start_idx:] + endpoints[:start_idx]
        logger.debug(
            "endpoint_rotation",
            chain=self.chain,
            start=rotated[0][:50] if rotated else "none",
        )

        client = await self._get_client()
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "eth_getLogs",
            "params": [filter_params],
        }

        best_result: list[dict[str, Any]] | None = None
        last_error: str | None = None

        for url in rotated:
            # Apply backoff for recently-failed endpoints
            await _apply_endpoint_backoff(url)

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
                            logger.debug(
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
                            _record_endpoint_failure(url)
                            continue
                        else:
                            continue
                    elif "error" in data:
                        last_error = data["error"].get("message", str(data))
                        _record_endpoint_failure(url)
                        logger.debug(
                            "rpc_error_skip",
                            url=url[:50],
                            error=last_error[:60],
                        )
                        continue
                elif resp.status_code == 429:
                    last_error = "rate_limited"
                    await _handle_rate_limit(url)
                    continue
                else:
                    last_error = f"http_{resp.status_code}"
                    _record_endpoint_failure(url)
                    logger.debug("rpc_http_error", url=url[:50], status=resp.status_code)
                    continue

            except httpx.TimeoutException:
                last_error = "timeout"
                _record_endpoint_failure(url)
                _metrics["rpc_errors"] += 1
                continue
            except (httpx.ConnectError, httpx.ReadError, httpx.WriteError) as exc:
                last_error = str(exc)[:60]
                _record_endpoint_failure(url)
                _metrics["rpc_errors"] += 1
                continue
            except Exception as exc:
                last_error = str(exc)[:80]
                _record_endpoint_failure(url)
                _metrics["rpc_errors"] += 1
                continue

        _metrics["blocks_processed"] += to_block - from_block + 1

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

    # ── RPC: Get Logs (Swap Events) ────────────────────────────────────

    async def fetch_swap_events(
        self,
        from_block: int,
        to_block: int,
    ) -> list[dict[str, Any]]:
        """
        Fetch DEX Swap events (V2 + V3) via eth_getLogs.

        First fetches PairCreated events from known factories to build a
        pair→(token0, token1) mapping, then fetches swap events. The mapping
        is used by _parse_swap_logs() to populate token0/token1 fields.

        Runs two parallel requests — one for the V2 swap topic and one for
        the V3 swap topic. Returns merged, deduplicated swap events.
        """
        if from_block > to_block:
            return []

        # Build pair→tokens mapping from PairCreated events first
        try:
            await self.fetch_pair_created_events(from_block, to_block)
        except Exception as exc:
            logger.warning(
                "pair_created_fetch_failed",
                chain=self.chain,
                error=str(exc)[:100],
            )
            return []

        # Clamp range to batch size
        if to_block - from_block > self.batch_size:
            to_block = from_block + self.batch_size

        client = await self._get_client()

        async def _fetch_topic(topic: str, label: str) -> list[dict[str, Any]]:
            filter_params = {
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "topics": [topic],
            }
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_getLogs",
                "params": [filter_params],
            }
            endpoints = GET_LOGS_ENDPOINTS.get(self.chain, [])
            if not endpoints:
                endpoints = [u for u, _ in self.router.get_available_endpoints(self.chain)]
            if not endpoints:
                return []

            # Rotate starting endpoint for load distribution
            start_idx = endpoints.index(_get_best_endpoint(endpoints))
            rotated = endpoints[start_idx:] + endpoints[:start_idx]

            best_result: list[dict[str, Any]] | None = None
            for url in rotated:
                # Apply backoff for recently-failed endpoints
                await _apply_endpoint_backoff(url)

                try:
                    resp = await client.post(url, json=body, timeout=RPC_TIMEOUT)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and "result" in data:
                            logs = data["result"]
                            if isinstance(logs, list):
                                parsed = self._parse_swap_logs(logs, from_block, to_block, topic)
                                logger.debug(
                                    "swap_logs_fetched",
                                    chain=self.chain,
                                    label=label,
                                    block_range=f"{from_block}-{to_block}",
                                    raw_logs=len(logs),
                                    parsed=len(parsed),
                                    endpoint=url.split("//")[1].split("/")[0][:30],
                                )
                                if best_result is None or len(parsed) > len(best_result):
                                    best_result = parsed
                                    if len(parsed) == len(logs):
                                        break
                    elif resp.status_code == 429:
                        await _handle_rate_limit(url)
                        continue
                    else:
                        _record_endpoint_failure(url)
                        _metrics["rpc_errors"] += 1
                        continue
                except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.WriteError):
                    _record_endpoint_failure(url)
                    _metrics["rpc_errors"] += 1
                    continue
                except Exception:
                    _record_endpoint_failure(url)
                    _metrics["rpc_errors"] += 1
                    continue

            return best_result or []

        # Fetch V2 and V3 swaps in parallel
        v2_task = _fetch_topic(SWAP_V2_TOPIC, "v2")
        v3_task = _fetch_topic(SWAP_V3_TOPIC, "v3")
        v2_results, v3_results = await asyncio.gather(v2_task, v3_task)

        # Merge and deduplicate
        seen_keys: set[str] = set()
        merged: list[dict[str, Any]] = []
        for evt in v2_results + v3_results:
            key = f"{evt['transactionHash']}:{evt['logIndex']}"
            if key not in seen_keys:
                seen_keys.add(key)
                merged.append(evt)

        return merged

    # ── PairCreated Fetch ────────────────────────────────────────────

    async def fetch_pair_created_events(
        self,
        from_block: int,
        to_block: int,
    ) -> None:
        """
        Fetch PairCreated events from known DEX factories.

        Builds a pair→(token0, token1) mapping in self._pair_tokens so that
        _parse_swap_logs() can populate token0/token1 fields in swap events.

        PairCreated(address indexed token0, address indexed token1, address pair, uint256)
          topic[0] = keccak256("PairCreated(address,address,address,uint256)")
          topic[1] = token0 (indexed, padded to 32 bytes)
          topic[2] = token1 (indexed, padded to 32 bytes)
          data[0:32] = pair address (padded to 32 bytes, right-aligned)
        """
        factories = FACTORY_ADDRESSES.get(self.chain, [])
        if not factories:
            logger.debug("no_factories_for_chain", chain=self.chain)
            return

        client = await self._get_client()

        for factory_addr in factories:
            filter_params = {
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "address": factory_addr,
                "topics": [PAIR_CREATED_TOPIC],
            }
            body = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_getLogs",
                "params": [filter_params],
            }

            endpoints = GET_LOGS_ENDPOINTS.get(self.chain, [])
            if not endpoints:
                endpoints = [u for u, _ in self.router.get_available_endpoints(self.chain)]
            if not endpoints:
                continue

            # Rotate starting endpoint for load distribution
            start_idx = endpoints.index(_get_best_endpoint(endpoints))
            rotated = endpoints[start_idx:] + endpoints[:start_idx]

            for url in rotated:
                try:
                    resp = await client.post(url, json=body, timeout=RPC_TIMEOUT)
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, dict) and "result" in data:
                            logs = data["result"]
                            if isinstance(logs, list):
                                for log in logs:
                                    topics = log.get("topics", [])
                                    data_hex = log.get("data", "0x")
                                    if len(topics) < 3:
                                        continue
                                    # token0 = topic[1], token1 = topic[2] (both padded)
                                    token0 = self._unpad_address(topics[1])
                                    token1 = self._unpad_address(topics[2])
                                    # pair address is rightmost 20 bytes of data[0:32]
                                    raw_data = data_hex.removeprefix("0x")
                                    if len(raw_data) < 64:
                                        continue
                                    pair_bytes = raw_data[24:64]  # last 20 bytes → 40 hex chars
                                    pair = "0x" + pair_bytes.lower()
                                    self._pair_tokens[pair] = (token0, token1)

                                logger.info(
                                    "pair_created_fetched",
                                    chain=self.chain,
                                    factory=factory_addr[:20],
                                    events=len(logs),
                                    total_pairs=len(self._pair_tokens),
                                )
                                break  # Got data from this endpoint
                    elif resp.status_code == 429:
                        await asyncio.sleep(2)
                        continue
                except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.WriteError):
                    continue
                except Exception as exc:
                    logger.debug(
                        "pair_created_error",
                        factory=factory_addr[:20],
                        error=str(exc)[:60],
                    )
                    continue

    # ── Swap Log Parser ──────────────────────────────────────────────

    def _parse_swap_logs(
        self,
        logs: list[dict[str, Any]],
        from_block: int,
        to_block: int,
        topic: str,
    ) -> list[dict[str, Any]]:
        """
        Parse raw eth_getLogs results for swap events (V2 or V3).

        V2 (Uniswap V2 style):
          topic0 = SWAP_V2_TOPIC
          topic1 = sender (indexed, address padded to 32 bytes)
          topics[2] = recipient (indexed, address padded to 32 bytes)
          data   = amount0In (uint256) + amount1In (uint256)
                 + amount0Out (uint256) + amount1Out (uint256)

        V3 (Uniswap V3 style):
          topic0 = SWAP_V3_TOPIC
          topic1 = sender (indexed, address padded to 32 bytes)
          topic2 = recipient (indexed, address padded to 32 bytes)
          data   = amount0 (int256) + amount1 (int256)
                 + sqrtPriceX96 (uint160) + liquidity (uint128) + tick (int24)
        """
        is_v2 = topic == SWAP_V2_TOPIC
        events: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        duplicates = 0

        for log in logs:
            if log.get("removed", False):
                continue

            topics = log.get("topics", [])
            if len(topics) < 3:
                continue

            # Parse block number
            block_hex = log.get("blockNumber", "0x0")
            try:
                block_num = int(block_hex, 16) if isinstance(block_hex, str) else int(block_hex)
            except (ValueError, TypeError):
                continue

            if block_num < from_block or block_num > to_block:
                continue

            log_index_hex = log.get("logIndex", "0x0")
            try:
                log_index = int(log_index_hex, 16) if isinstance(log_index_hex, str) else int(log_index_hex)
            except (ValueError, TypeError):
                log_index = 0

            tx_hash = log.get("transactionHash", "")
            dedup_key = f"{tx_hash}:{log_index}"
            if dedup_key in seen_keys:
                duplicates += 1
                continue
            seen_keys.add(dedup_key)

            data_hex = log.get("data", "0x")
            raw = data_hex.removeprefix("0x")

            if is_v2:
                # V2 swap decode
                sender = self._unpad_address(topics[1])
                recipient = (
                    self._unpad_address(topics[2]) if len(topics) > 2 else "0x0000000000000000000000000000000000000000"
                )

                amount0_in = "0"
                amount1_in = "0"
                amount0_out = "0"
                amount1_out = "0"

                if len(raw) >= 256:  # 4 * 32 bytes = 256 hex chars
                    amount0_in = str(int(raw[0:64], 16))
                    amount1_in = str(int(raw[64:128], 16))
                    amount0_out = str(int(raw[128:192], 16))
                    amount1_out = str(int(raw[192:256], 16))

                dex_type = "v2"
                dex_name = "uniswap_v2"

            else:
                # V3 swap decode — store raw values
                sender = self._unpad_address(topics[1])
                recipient = (
                    self._unpad_address(topics[2]) if len(topics) > 2 else "0x0000000000000000000000000000000000000000"
                )

                amount0_in = "0"
                amount1_in = "0"
                amount0_out = "0"
                amount1_out = "0"

                if len(raw) >= 256:  # amount0 + amount1 + sqrtPriceX96 + liquidity + tick
                    amount0_in = str(int(raw[0:64], 16))
                    amount1_in = str(int(raw[64:128], 16))
                    amount0_out = "0"
                    amount1_out = "0"

                dex_type = "v3"
                dex_name = "uniswap_v3"

            # Resolve token0/token1 from pair→tokens mapping (built from PairCreated events)
            pair = log.get("address", "").lower()
            if pair in self._pair_tokens:
                token0, token1 = self._pair_tokens[pair]
            else:
                token0, token1 = "", ""

            events.append(
                {
                    "type": "swap",
                    "dex_type": dex_type,
                    "dex": dex_name,
                    "chain": self.chain,
                    "blockNumber": block_num,
                    "blockTimestamp": "",
                    "transactionHash": tx_hash,
                    "logIndex": log_index,
                    "sender": sender,
                    "recipient": recipient,
                    "token0": token0,
                    "token1": token1,
                    "amount0In": amount0_in,
                    "amount1In": amount1_in,
                    "amount0Out": amount0_out,
                    "amount1Out": amount1_out,
                    "pairAddress": log.get("address", ""),
                    "price_usd": 0.0,
                    "volume_usd": 0.0,
                    "source": "lite_indexer",
                }
            )

            if len(events) >= MAX_EVENTS_PER_BATCH:
                logger.warning("swap_event_cap_reached", count=MAX_EVENTS_PER_BATCH)
                break

        if duplicates:
            logger.debug("swap_dedup_removed", chain=self.chain, count=duplicates)

        return events

    # ── DEX NDJSON Writer ─────────────────────────────────────────────

    def write_dex_ndjson(self, events: list[dict[str, Any]]) -> int:
        """
        Append swap events to /srv/data/chain-indexer/{chain}/dex/{date}/events.jsonl.
        """
        if not events:
            return 0

        if not self._check_disk_before_write("dex_ndjson"):
            return 0

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        out_dir = CHAIN_INDEXER_DIR / self.chain / "dex" / today
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "events.jsonl"

        try:
            lines = [json.dumps(e, ensure_ascii=False) + "\n" for e in events]
            bytes_written = self._write_ndjson_lines(lines, out_path)

            logger.info(
                "dex_ndjson_written",
                chain=self.chain,
                path=str(out_path),
                events=len(events),
                bytes=bytes_written,
            )
            return bytes_written
        except OSError as exc:
            logger.error("dex_ndjson_write_failed", chain=self.chain, error=str(exc))
            return 0

    # ── DEX ClickHouse Writer ─────────────────────────────────────────

    def write_dex_clickhouse(self, events: list[dict[str, Any]]) -> int:
        """
        Insert swap events into ClickHouse wallet_memory.dex_events.
        """
        if not events:
            return 0

        client = self._get_ch_client()
        if not client:
            logger.warning("dex_clickhouse_skipped", chain=self.chain)
            return 0

        # Ensure source column exists
        self._ensure_source_column(client, CH_DEX_TABLE)

        now = datetime.now(UTC)
        rows: list[tuple] = []

        for event in events:
            ts_str = event.get("blockTimestamp", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    ts = now
            else:
                ts = now

            version = event.get("dex_type", "v2")
            # Use In amounts for amount0/amount1 which match the table schema
            # (these are raw uint256 string values from the event log)
            amt0 = event.get("amount0In", "0") or "0"
            amt1 = event.get("amount1In", "0") or "0"

            rows.append(
                (
                    "swap",  # type — always 'swap' for swap events
                    self.chain,
                    event["blockNumber"],
                    ts,
                    event["transactionHash"],  # table column: tx_hash
                    event["logIndex"],
                    event.get("pairAddress", ""),  # table column: contract_address
                    event.get("dex", ""),
                    version,  # table column: version
                    event.get("token0", ""),
                    event.get("token1", ""),
                    event.get("sender", ""),
                    event.get("recipient", ""),
                    event.get("amount0In", "0"),
                    event.get("amount1In", "0"),
                    event.get("amount0Out", "0"),
                    event.get("amount1Out", "0"),
                    amt0,  # amount0
                    amt1,  # amount1
                    0,  # whale — UInt8, not detected yet
                    now,
                    event.get("source", "lite_indexer"),
                )
            )

        try:
            client.execute(
                f"INSERT INTO {CH_DEX_TABLE} "
                "(type, chain, block_number, block_timestamp, tx_hash, log_index, "
                "contract_address, dex, version, token0, token1, sender, recipient, "
                "amount0_in, amount1_in, amount0_out, amount1_out, "
                "amount0, amount1, whale, ingested_at, source) VALUES",
                rows,
            )
            logger.info("dex_clickhouse_inserted", chain=self.chain, rows=len(rows))
            _metrics["clickhouse_inserts"] += 1
            return len(rows)
        except Exception as exc:
            _metrics["clickhouse_errors"] += 1
            logger.error(
                "dex_clickhouse_insert_failed",
                chain=self.chain,
                rows=len(rows),
                error=str(exc)[:150],
            )
            return 0

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

            # Session-level dedup (persists across sync_loop calls)
            if _is_duplicate(tx_hash, log_index):
                duplicates += 1
                logger.debug("session_dedup_removed", tx_hash=tx_hash[:20], log_index=log_index)
                continue

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
                    "source": "lite_indexer",
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

        for event in events:
            ts = timestamps.get(event["blockNumber"])
            if ts:
                event["blockTimestamp"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
            else:
                event["blockTimestamp"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        return events

    # ── Output: NDJSON ──────────────────────────────────────────────

    @staticmethod
    def _write_ndjson_lines(lines: list[str], out_path: Path) -> int:
        """Write NDJSON lines to a file in one batch. Returns bytes written."""
        bytes_written = 0
        with open(out_path, "a") as f:
            for line in lines:
                f.write(line)
                bytes_written += len(line.encode("utf-8"))
        return bytes_written

    def _check_disk_before_write(self, label: str = "") -> bool:
        """Check both disk cap and free space before a write operation."""
        if not self.check_disk_cap():
            logger.error("write_aborted_disk_cap", chain=self.chain, label=label)
            return False
        if not _check_disk_space():
            logger.error("write_aborted_disk_space", chain=self.chain, label=label)
            return False
        return True

    def write_ndjson(self, events: list[dict[str, Any]]) -> int:
        """
        Append events to /srv/data/chain-indexer/{chain}/{date}/events.jsonl.

        Returns bytes written, or 0 on failure.
        """
        if not events:
            return 0

        if not self._check_disk_before_write("ndjson"):
            return 0

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        out_dir = CHAIN_INDEXER_DIR / self.chain / today
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "events.jsonl"

        try:
            lines = [json.dumps(e, ensure_ascii=False) + "\n" for e in events]
            bytes_written = self._write_ndjson_lines(lines, out_path)

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

    def _ensure_source_column(self, client: Any, table: str) -> None:
        """Ensure source column exists in ClickHouse tables. Skip if already present."""
        try:
            # Check if column exists first — avoids ALTER spam on every cycle
            result = client.execute(
                "SELECT name FROM system.columns "
                "WHERE database = 'wallet_memory' AND table = %(table)s AND name = 'source'",
                {"table": table.split(".")[-1]},
            )
            if result:
                return  # Column already exists, skip ALTER

            # Only ALTER if it doesn't exist
            client.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS source String DEFAULT ''")
            logger.info("source_column_added", table=table)
        except Exception as exc:
            # Suppress — column already exists or insufficient privileges
            if "already exists" in str(exc) or "privileges" in str(exc).lower():
                return
            logger.debug("source_column_skip", error=str(exc)[:80])

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

        # Ensure source column exists
        self._ensure_source_column(client, CH_TABLE)

        now = datetime.now(UTC)
        rows: list[tuple] = []

        for event in events:
            ts_str = event.get("blockTimestamp", "")
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
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
                    event.get("source", "lite_indexer"),
                )
            )

        try:
            client.execute(
                f"INSERT INTO {CH_TABLE} "
                "(chain, block_number, tx_hash, from_address, to_address, "
                "token_address, value, block_timestamp, log_index, "
                "token_symbol, token_name, ingested_at, source) VALUES",
                rows,
            )
            logger.info("clickhouse_inserted", chain=self.chain, rows=len(rows))
            _metrics["clickhouse_inserts"] += 1
            return len(rows)
        except Exception as exc:
            _metrics["clickhouse_errors"] += 1
            logger.error(
                "clickhouse_insert_failed",
                chain=self.chain,
                rows=len(rows),
                error=str(exc)[:150],
            )
            return 0

    # ── Block Range Determination ────────────────────────────────────

    async def _determine_block_range(self, summary: dict[str, Any]) -> tuple[int, int] | None:
        """
        Determine the block range for this sync cycle.

        When --start-from-head is active, we ignore the state file and
        start indexing from (chain_tip - HEAD_BUFFER) — just like Ponder,
        Subsquid, and Envio. No backfill from genesis.

        Returns (from_block, to_block) or None if we should abort.
        """
        # Fast path: start from near the chain head (no backfill)
        if self.start_from_head:
            tip = await self.fetch_block_number()
            if tip is None:
                summary["status"] = "no_rpc_available"
                summary["errors"].append("fetch_block_number failed")
                logger.error("sync_aborted_no_rpc", chain=self.chain)
                return None
            from_block = max(0, tip - HEAD_BUFFER)
            to_block = tip
            logger.info(
                "starting_from_head",
                chain=self.chain,
                from_block=from_block,
                tip=tip,
                head_buffer=HEAD_BUFFER,
            )
            return from_block, to_block

        # Normal path: resume from state file or override
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
            return None

        to_block = self._override_to if self._override_to is not None else current_block

        if from_block >= to_block:
            logger.info("caught_up", chain=self.chain, block=current_block)
            summary["status"] = "caught_up"
            return None

        if to_block - from_block > self.batch_size:
            to_block = from_block + self.batch_size
            logger.info(
                "batch_limited",
                chain=self.chain,
                from_block=from_block,
                to_block=to_block,
            )

        return from_block, to_block

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

        # 1. Disk cap + disk space checks
        if not self.check_disk_cap():
            summary["status"] = "disk_cap_exceeded"
            summary["errors"].append("disk cap exceeded")
            logger.error("sync_aborted_disk_cap", chain=self.chain)
            return summary

        if not _check_disk_space():
            summary["status"] = "disk_full"
            summary["errors"].append("disk space critically low")
            logger.error("sync_aborted_disk_space", chain=self.chain)
            return summary

        # 2. Determine block range
        block_range = await self._determine_block_range(summary)
        if block_range is None:
            return summary
        from_block, to_block = block_range

        # 3. Fetch transfer events
        logger.info(
            "sync_start",
            chain=self.chain,
            from_block=from_block,
            to_block=to_block,
        )

        events = await self.fetch_transfers(from_block, to_block)
        if events:
            summary["events_fetched"] = len(events)

        # 4. Fetch DEX swap events (if enabled)
        dex_events: list[dict[str, Any]] = []
        if self.dex_enabled:
            logger.info(
                "dex_sync_start",
                chain=self.chain,
                from_block=from_block,
                to_block=to_block,
            )
            dex_events = await self.fetch_swap_events(from_block, to_block)
            summary["dex_events_fetched"] = len(dex_events)

        if not events and not dex_events:
            self.save_state(to_block, 0)
            summary["status"] = "no_events"
            summary["duration_seconds"] = time.monotonic() - start_time
            return summary

        # 5. Enrich with timestamps (both transfer and dex events)
        if events:
            events = await self._enrich_timestamps(events)
        if dex_events:
            dex_events = await self._enrich_timestamps(dex_events)

        # 6. Write NDJSON
        bytes_written = 0
        if events:
            bytes_written = self.write_ndjson(events)
            if bytes_written:
                summary["events_written_ndjson"] = len(events)
                summary["bytes_written"] = bytes_written

        dex_bytes_written = 0
        if dex_events:
            dex_bytes_written = self.write_dex_ndjson(dex_events)
            if dex_bytes_written:
                summary["dex_events_written_ndjson"] = len(dex_events)
                summary["dex_bytes_written"] = dex_bytes_written

        # 7. Write ClickHouse
        ch_inserted = 0
        if events:
            ch_inserted = self.write_clickhouse(events)
            summary["events_written_ch"] = ch_inserted

        dex_ch_inserted = 0
        if dex_events:
            dex_ch_inserted = self.write_dex_clickhouse(dex_events)
            summary["dex_events_written_ch"] = dex_ch_inserted

        # 8. Update metrics
        if events:
            _metrics["transfers_written"] += ch_inserted or len(events)
        if dex_events:
            _metrics["dex_swaps_written"] += dex_ch_inserted or len(dex_events)

        # 9. Save state
        total_events = len(events) + len(dex_events)
        self.save_state(to_block, total_events)

        summary["duration_seconds"] = round(time.monotonic() - start_time, 2)
        logger.info(
            "sync_complete",
            chain=self.chain,
            transfer_events=len(events),
            dex_events=len(dex_events),
            ndjson=bool(bytes_written),
            dex_ndjson=bool(dex_bytes_written),
            clickhouse=bool(ch_inserted),
            dex_clickhouse=bool(dex_ch_inserted),
            duration_s=summary["duration_seconds"],
        )

        _log_metrics()
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
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in daemon mode — continuously index all chains",
    )
    parser.add_argument(
        "--chains",
        type=str,
        default="ethereum,base,bsc,polygon,arbitrum,optimism,avalanche,linea,scroll,zksync,mantle,gnosis",
        help="Comma-separated list of chains to index in daemon mode",
    )
    parser.add_argument(
        "--dex",
        action="store_true",
        help="Enable DEX swap event indexing (V2 + V3)",
    )
    parser.add_argument(
        "--start-from-head",
        action="store_true",
        help="Start indexing from chain tip instead of backfilling from genesis. "
        "Ignores any existing state file and begins from (tip - 100). "
        "Equivalent to Ponder/Subsquid/Envio 'from latest block' mode.",
    )
    return parser.parse_args(argv)


async def main_daemon(
    chains: list[str],
    dex_enabled: bool = False,
    disk_cap: int = DISK_CAP_BYTES,
    start_from_head: bool = False,
) -> int:
    """
    Daemon mode: continuously index all configured chains in parallel.

    Each chain gets its own LiteIndexer instance with independent state
    tracking and polling interval. Runs until SIGTERM/SIGINT.
    """
    logger.info(
        "daemon_start",
        chains=chains,
        dex=dex_enabled,
        disk_cap=disk_cap,
        start_from_head=start_from_head,
    )

    # Create per-chain indexers
    indexers = {}
    for chain in chains:
        indexer = LiteIndexer(chain=chain, disk_cap=disk_cap, start_from_head=start_from_head)
        indexer.dex_enabled = dex_enabled
        indexers[chain] = indexer

    running = True
    shutdown_event = asyncio.Event()

    def _handle_signal(sig: int, _frame: Any) -> None:
        nonlocal running
        sig_name = signal.Signals(sig).name
        logger.info("daemon_signal_received", signal=sig_name)
        running = False
        shutdown_event.set()

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig: _handle_signal(s, None))
        except (NotImplementedError, RuntimeError):
            # Fallback for platforms without add_signal_handler
            signal.signal(sig, _handle_signal)

    max_poll_interval = max(CHAIN_POLL_INTERVALS.get(c, 15) for c in chains) if chains else 15
    logger.info("daemon_poll_interval", max_seconds=max_poll_interval)

    while running:
        start_cycle = time.monotonic()

        async def _sync_chain(chain: str) -> dict[str, Any]:
            indexer = indexers[chain]
            try:
                return await indexer.sync_loop()
            except Exception as exc:
                logger.error("daemon_chain_failed", chain=chain, error=str(exc))
                return {"chain": chain, "status": "error", "error": str(exc)}

        # Run chains with adaptive concurrency — fast chains don't wait for slow ones
        tasks = {}
        for c in chains:
            tasks[c] = asyncio.create_task(_sync_chain(c), name=f"sync-{c}")

        results = []
        for c in chains:
            try:
                result = await tasks[c]
                results.append(result)
            except Exception as exc:
                results.append({"chain": c, "status": "error", "error": str(exc)})

        ok_count = sum(
            1 for r in results if isinstance(r, dict) and r.get("status") in ("ok", "no_events", "caught_up")
        )
        error_count = sum(
            1 for r in results if isinstance(r, Exception) or (isinstance(r, dict) and r.get("status") == "error")
        )

        elapsed = time.monotonic() - start_cycle
        logger.info(
            "daemon_cycle_complete",
            chains=len(chains),
            ok=ok_count,
            errors=error_count,
            cycle_s=round(elapsed, 2),
        )

        # Use longest chain poll interval as base, subtract elapsed time
        remaining = max_poll_interval - elapsed
        if remaining > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(shutdown_event.wait(), timeout=remaining)

    logger.info("daemon_shutdown")
    return 0 if error_count == 0 else 1


async def async_main(args: argparse.Namespace) -> int:
    """Async entry point."""
    # Daemon mode
    if args.daemon:
        chains = [c.strip() for c in args.chains.split(",") if c.strip()]
        return await main_daemon(
            chains,
            dex_enabled=args.dex,
            disk_cap=args.disk_cap,
            start_from_head=args.start_from_head,
        )

    # Single-chain mode
    indexer = LiteIndexer(
        chain=args.chain,
        from_block=args.from_block,
        to_block=args.to_block,
        batch_size=args.batch_size,
        disk_cap=args.disk_cap,
        start_from_head=args.start_from_head,
    )
    indexer.dex_enabled = args.dex

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
    "async_main",
    "main",
    "main_daemon",
    "parse_args",
]
